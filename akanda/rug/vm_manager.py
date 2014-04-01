from datetime import datetime
import netaddr
import time

from oslo.config import cfg

from akanda.rug.api import configuration
from akanda.rug.api import akanda_client as router_api
from akanda.rug.api.nova import RouterDeleting

DOWN = 'down'
BOOTING = 'booting'
UP = 'up'
CONFIGURED = 'configured'
RESTART = 'restart'


class VmManager(object):
    def __init__(self, router_id, tenant_id, log, worker_context):
        self.router_id = router_id
        self.tenant_id = tenant_id
        self.log = log
        self.state = DOWN
        self.router_obj = None
        self.last_boot = None
        # FIXME: Probably need to pass context here
        self.update_state(worker_context, silent=True)

    def update_state(self, worker_context, silent=False):
        self._ensure_cache(worker_context)

        if self.router_obj.management_port is None:
            self.state = DOWN
            return self.state

        addr = _get_management_address(self.router_obj)
        for i in xrange(cfg.CONF.max_retries):
            if router_api.is_alive(addr, cfg.CONF.akanda_mgt_service_port):
                if self.state != CONFIGURED:
                    self.state = UP
                break
            if not silent:
                self.log.debug(
                    'Alive check failed. Attempt %d of %d',
                    i,
                    cfg.CONF.max_retries
                )
            time.sleep(cfg.CONF.retry_delay)
        else:
            self.state = DOWN
            if self.last_boot:
                seconds_since_boot = (
                    datetime.utcnow() - self.last_boot
                ).seconds
                if seconds_since_boot < cfg.CONF.boot_timeout:
                    self.state = BOOTING
                else:
                    # If the VM was created more than `boot_timeout` seconds
                    # ago, log an error and leave the state set to DOWN
                    self.last_boot = None
                    self.log.info(
                        'Router is DOWN.  Created over %d secs ago.',
                        cfg.CONF.boot_timeout)

        return self.state

    def boot(self, worker_context):
        # FIXME: Modify _ensure_cache() so we can call it with a force
        # flag instead of bypassing it.
        self.router_obj = worker_context.neutron.get_router_detail(
            self.router_id
        )

        self.log.info('Booting router')
        self.state = DOWN

        self._ensure_provider_ports(self.router_obj, worker_context)

        try:
            # In the event that the current akanda instance isn't deleted
            # cleanly (which we've seen in certain circumstances, like
            # hypervisor failures), be proactive and attempt to clean up the
            # router ports manually.  This helps avoid a situation where the
            # rug repeatedly attempts to plug stale router ports into the newly
            # created akanda instance (and fails).
            router = self.router_obj
            instance = worker_context.nova_client.get_instance(router)
            if instance is not None:
                for p in router.ports:
                    if p.device_id == instance.id:
                        worker_context.neutron.clear_device_id(p)
            worker_context.nova_client.reboot_router_instance(router)
        except RouterDeleting:
            self.log.info('Previous router is deleting')
            return
        except:
            self.log.exception('Router failed to start boot')
            return
        else:
            self.last_boot = datetime.utcnow()

    def check_boot(self, worker_context):
        ready_states = (UP, CONFIGURED)
        if self.update_state(worker_context, silent=True) in ready_states:
            self.log.info('Router has booted, attempting initial config')
            self.configure(worker_context, BOOTING, attempts=1, silent=True)
            return self.state == CONFIGURED
        self.log.debug('Router is %s' % self.state.upper())
        return False

    def stop(self, worker_context):
        self._ensure_cache(worker_context)
        self.log.info('Destroying router')

        nova_client = worker_context.nova_client
        nova_client.destroy_router_instance(self.router_obj)

        start = time.time()
        while time.time() - start < cfg.CONF.boot_timeout:
            if not nova_client.get_router_instance_status(self.router_obj):
                self.state = DOWN
                return
            self.log.debug('Router has not finished stopping')
            time.sleep(cfg.CONF.retry_delay)
        self.log.error(
            'Router failed to stop within %d secs',
            cfg.CONF.boot_timeout)

    def configure(self, worker_context, failure_state=RESTART, attempts=None,
                  silent=False):
        self.log.debug('Begin router config')
        self.state = UP
        attempts = attempts or cfg.CONF.max_retries

        # FIXME: This might raise an error, which doesn't mean the
        # *router* is broken, but does mean we can't update it.
        # Change the exception to something the caller can catch
        # safely.
        self.router_obj = worker_context.neutron.get_router_detail(
            self.router_id
        )

        addr = _get_management_address(self.router_obj)

        # FIXME: This should raise an explicit exception so the caller
        # knows that we could not talk to the router (versus the issue
        # above).
        interfaces = router_api.get_interfaces(
            addr,
            cfg.CONF.akanda_mgt_service_port
        )

        if not self._verify_interfaces(self.router_obj, interfaces):
            # FIXME: Need a REPLUG state when we support hot-plugging
            # interfaces.
            self.state = failure_state
            return

        # FIXME: Need to catch errors talking to neutron here.
        config = configuration.build_config(
            worker_context.neutron,
            self.router_obj,
            interfaces
        )
        self.log.debug('preparing to update config to %r', config)

        for i in xrange(attempts):
            try:
                router_api.update_config(
                    addr,
                    cfg.CONF.akanda_mgt_service_port,
                    config
                )
            except Exception:
                if not silent:
                    if i == attempts - 1:
                        # Only log the traceback if we encounter it many times.
                        self.log.exception('failed to update config')
                    else:
                        self.log.debug(
                            'failed to update config, attempt %d',
                            i
                        )
                time.sleep(cfg.CONF.retry_delay)
            else:
                self.state = CONFIGURED
                self.log.info('Router config updated')
                return
        else:
            # FIXME: We failed to configure the router too many times,
            # so restart it.
            self.state = failure_state

    def _ensure_cache(self, worker_context):
        if self.router_obj:
            return
        self.router_obj = worker_context.neutron.get_router_detail(
            self.router_id
        )

    def _verify_interfaces(self, logical_config, interfaces):
        router_macs = set((iface['lladdr'] for iface in interfaces))
        self.log.debug('MACs found: %s', ', '.join(sorted(router_macs)))

        expected_macs = set(p.mac_address
                            for p in logical_config.internal_ports)
        expected_macs.add(logical_config.management_port.mac_address)
        expected_macs.add(logical_config.external_port.mac_address)
        self.log.debug('MACs expected: %s', ', '.join(sorted(expected_macs)))

        return router_macs == expected_macs

    def _ensure_provider_ports(self, router, worker_context):
        if router.management_port is None:
            self.log.debug('Adding management port to router')
            mgt_port = worker_context.neutron.create_router_management_port(
                router.id
            )
            router.management_port = mgt_port

        if router.external_port is None:
            # FIXME: Need to do some work to pick the right external
            # network for a tenant.
            self.log.debug('Adding external port to router')
            ext_port = worker_context.neutron.create_router_external_port(
                router
            )
            router.external_port = ext_port
        return router


def _get_management_address(router):
    network = netaddr.IPNetwork(cfg.CONF.management_prefix)

    tokens = ['%02x' % int(t, 16)
              for t in router.management_port.mac_address.split(':')]
    eui64 = int(''.join(tokens[0:3] + ['ff', 'fe'] + tokens[3:6]), 16)

    # the bit inversion is required by the RFC
    return str(netaddr.IPAddress(network.value + (eui64 ^ 0x0200000000000000)))

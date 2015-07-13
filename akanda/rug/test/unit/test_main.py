# Copyright 2014 DreamHost, LLC
#
# Author: DreamHost, LLC
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import sys
import socket

import mock
import unittest2 as unittest

from akanda.rug import main
from akanda.rug import notifications as ak_notifications


@mock.patch('akanda.rug.main.cfg')
@mock.patch('akanda.rug.main.neutron_api')
@mock.patch('akanda.rug.main.multiprocessing')
@mock.patch('akanda.rug.main.notifications')
@mock.patch('akanda.rug.main.scheduler')
@mock.patch('akanda.rug.main.populate')
@mock.patch('akanda.rug.main.health')
class TestMainPippo(unittest.TestCase):
    def test_shuffle_notifications(self, health, populate, scheduler,
                                   notifications, multiprocessing, neutron_api,
                                   cfg):
        queue = mock.Mock()
        queue.get.side_effect = [
            ('9306bbd8-f3cc-11e2-bd68-080027e60b25', 'message'),
            KeyboardInterrupt,
        ]
        sched = scheduler.Scheduler.return_value
        main.shuffle_notifications(queue, sched)
        sched.handle_message.assert_called_once_with(
            '9306bbd8-f3cc-11e2-bd68-080027e60b25',
            'message'
        )

    def test_shuffle_notifications_error(
            self, health, populate, scheduler, notifications,
            multiprocessing, neutron_api, cfg):
        queue = mock.Mock()
        queue.get.side_effect = [
            ('9306bbd8-f3cc-11e2-bd68-080027e60b25', 'message'),
            RuntimeError,
            KeyboardInterrupt,
        ]
        sched = scheduler.Scheduler.return_value
        main.shuffle_notifications(queue, sched)
        sched.handle_message.assert_called_once_with(
            '9306bbd8-f3cc-11e2-bd68-080027e60b25', 'message'
        )

    @mock.patch('akanda.rug.main.shuffle_notifications')
    def test_ensure_local_service_port(self, shuffle_notifications, health,
                                       populate, scheduler, notifications,
                                       multiprocessing, neutron_api, cfg):
        main.main()
        neutron = neutron_api.Neutron.return_value
        neutron.ensure_local_service_port.assert_called_once_with()

    @mock.patch('akanda.rug.main.shuffle_notifications')
    def test_ceilometer_disabled(self, shuffle_notifications, health,
                                 populate, scheduler, notifications,
                                 multiprocessing, neutron_api, cfg):
        cfg.CONF.ceilometer.enabled = False
        notifications.Publisher = mock.Mock(spec=ak_notifications.Publisher)
        notifications.NoopPublisher = mock.Mock(
            spec=ak_notifications.NoopPublisher)
        main.main()
        self.assertEqual(len(notifications.Publisher.mock_calls), 0)
        self.assertEqual(len(notifications.NoopPublisher.mock_calls), 1)

    @mock.patch('akanda.rug.main.shuffle_notifications')
    def test_ceilometer_enabled(self, shuffle_notifications, health,
                                populate, scheduler, notifications,
                                multiprocessing, neutron_api, cfg):
        cfg.CONF.ceilometer.enabled = True
        notifications.Publisher = mock.Mock(spec=ak_notifications.Publisher)
        notifications.NoopPublisher = mock.Mock(
            spec=ak_notifications.NoopPublisher)
        main.main()
        self.assertEqual(len(notifications.Publisher.mock_calls), 1)
        self.assertEqual(len(notifications.NoopPublisher.mock_calls), 0)


@mock.patch('akanda.rug.main.cfg')
@mock.patch('akanda.rug.api.neutron.importutils')
@mock.patch('akanda.rug.api.neutron.AkandaExtClientWrapper')
@mock.patch('akanda.rug.main.multiprocessing')
@mock.patch('akanda.rug.main.notifications')
@mock.patch('akanda.rug.main.scheduler')
@mock.patch('akanda.rug.main.populate')
@mock.patch('akanda.rug.main.health')
@mock.patch('akanda.rug.main.shuffle_notifications')
@mock.patch('akanda.rug.api.neutron.get_local_service_ip')
class TestMainExtPortBinding(unittest.TestCase):

    @unittest.skipIf(
        sys.platform != 'linux2',
        'unsupported platform'
    )
    def test_ensure_local_port_host_binding(
            self, get_local_service_ip, shuffle_notifications, health,
            populate, scheduler, notifications, multiprocessing,
            akanda_wrapper, importutils, cfg):

        cfg.CONF.plug_external_port = False

        def side_effect(**kwarg):
            return {'ports': {}}
        akanda_wrapper.return_value.list_ports.side_effect = side_effect

        main.main()
        args, kwargs = akanda_wrapper.return_value.create_port.call_args
        port = args[0]['port']
        self.assertIn('binding:host_id', port)
        self.assertEqual(port['binding:host_id'], socket.gethostname())

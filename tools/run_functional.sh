#!/bin/bash -xe

FUNC_TEST_DIR=$(dirname $0)/../astara/test/functional/
CONFIG_FILE=$FUNC_TEST_DIR/test.conf

APPLIANCE_API_PORT=${APPLIANCE_API_PORT:-5000}
SERVICE_TENANT_NAME=${SERVICE_TENANT_NAME:-service}
if [ -z "$SERVICE_TENANT_ID" ]; then
    SERVICE_TENANT_ID="$(keystone tenant-list | grep $SERVICE_TENANT_NAME | awk '{ print $2 }')"
fi

# Functional tests require a test astara router be created prior to the test
# run. Devstack does this, but you may specify another here.  If not specified,
# the ID of the devstack created router will be used.
ASTARA_TEST_ROUTER_UUID=${ASTARA_TEST_ROUTER_UUID:-''}

function find_router() {
    # Find the UUID of the astara router created by devstack.
    router=$(neutron router-list | grep "ak-" | awk '{ print $2 }')
    if [ $(echo "$router" | wc -l) -gt 1 ]; then
        echo "ERROR: Found multiple astara routers, cannot continue."
        exit 1
    elif [ -z "$router" ]; then
        echo "ERROR: Could not locate astara router."
        exit 1
    fi
    echo $router
}


cat <<END >$CONFIG_FILE
[functional]
appliance_active_timeout=480
os_auth_url=$OS_AUTH_URL
os_username=$OS_USERNAME
os_password=$OS_PASSWORD
os_tenant_name=$OS_TENANT_NAME
service_tenant_name=$SERVICE_TENANT_NAME
service_tenant_id=$SERVICE_TENANT_ID
appliance_api_port=$APPLIANCE_API_PORT
END

if [ -z "$ASTARA_TEST_ROUTER_UUID" ]; then
    ASTARA_TEST_ROUTER_UUID="$(find_router)"
fi
echo "astara_test_router_uuid=$ASTARA_TEST_ROUTER_UUID" >>$CONFIG_FILE

tox -e  functional
rc=$?

if [[ $rc != 0 ]]; then
    # Attempt to catch (LP: #1524962)
    dsvm_log="/opt/stack/logs/screen-astara.txt"
    if tail -n1 $dsvm_log | grep "Updating config for"; then
        echo "astara-appliance config update hung. see bug #1524962"
    fi
fi
exit $rc

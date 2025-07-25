'''Transceiver Onboarding Test - Link Related Tests

xcvr_onboarding_test_plan   sect. 1.1 link related tests

These tests aim to validate the link status and stability of transceivers 
under various conditions.
'''
import os
import sys
import time

from sonic_platform.platform import Platform
import logging

from api_wrapper    import *   # wrappers for (optoe, sfp, sfp_base, xcvr_api, cmis, ...)
from cli_wrapper    import *   # wrappers for CLI
from util_wrapper   import *   # wrappers replacing platform_tests/sfp/util.py
from test_cfg       import *   # wrappers dealing with test config file etc.


# Local Constants
# wait time for port to power DOWN/UP
_DELAY_AFTER_IF_SHUTDOWN_S  = 3.0

_POLL_PERIOD_S              = 1.0
_MAX_WAIT_FOR_LINK_UP_S     = 60.0
_MAX_WAIT_FOR_LINK_UP_COHERENT_S = 180

# small extra delay for LLDP table update after link up(?)
_DELAY_LLDP_UPDATE_S        = 1.0

# stress test settings
_STRESS_TEST_LOOPS      = 100
_STRESS_TEST_LOOP_DBG   = 1     # TEMPORARY DEBUG

# CLI commands
cmd_lldp_table          = 'show lldp table '



def test_check_link_status(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Validate link status using CLI configuration

    Issue CLI command to shutdown port
    Ensure the link goes down

    Issue CLI command to startup port
    Ensure the link is up
    Ensure the port appears in the LLDP table.
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check Link Status")

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:

            if not cli_interface_present(intf):
                print('%s not present? skipping test' % (intf))
                continue

            # shutdown port
            cli_interface_shutdown(intf)
            time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

            # Ensure the link goes down
            up = cli_interface_oper_status_up(intf)
            assert not up, '%s not down' % (intf)

            # startup port
            cli_interface_startup(intf)
            
            # Ensure the link is up
            timeout = _MAX_WAIT_FOR_LINK_UP_S
            if is_coherent(intf):
                timeout = _MAX_WAIT_FOR_LINK_UP_COHERENT_S
            t_limit = time.time() + timeout
            up = False
            while time.time() < t_limit:
                time.sleep(_POLL_PERIOD_S)
                timeout -= _POLL_PERIOD_S
                up = cli_interface_oper_status_up(intf)
                if up:
                    break
            assert up, '%s not up after %fs' % (intf, timeout)

            # Ensure the port appears in the LLDP table.
            # small extra delay to make sure LLDP table is updated(?)
            time.sleep(_DELAY_LLDP_UPDATE_S)

            cmdstr  = cmd_lldp_table
            clistr  = cli_wrap(cmdstr)
            lines = clistr.splitlines()
            found = False
            for line in lines:
                if intf in line:
                    found = True
                    break
            assert found, '%s not in lldp table' % intf

            print('test_check_link_status ', intf, ' done') # TEMPORARY DEBUG


def test_check_stress_link_status(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Stress test for link status validation.

    In a loop, issue startup/shutdown command 100 times.
    Ensure link status toggles up/down appropriately with each startup/shutdown. 
    Ensure ports appear in the LLDP table when the link is up.
    
    Because this is a test with lots of repeated loops, the structure is different 
    to reduce overhead.
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Stress Test Link Status")

    # check which ports to test just once
    interfaces_to_test  = []
    num_interfaces      = 0
    timeout             = _MAX_WAIT_FOR_LINK_UP_S

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            if not cli_interface_present(intf):
                print('%s not present? skipping this' % (intf))
                continue
            interfaces_to_test.append(intf)
            num_interfaces += 1
            if is_coherent(intf):
                timeout = _MAX_WAIT_FOR_LINK_UP_COHERENT_S

    # then do the test loop
    if _STRESS_TEST_LOOP_DBG:
        print('test_check_stress_link_status start (%d ports, %d loops)' % (num_interfaces, _STRESS_TEST_LOOPS))

    for loopcnt in range(_STRESS_TEST_LOOPS):
        if _STRESS_TEST_LOOP_DBG:
            print('test_check_stress_link_status loop %d...' % (loopcnt))

        # shutdown ports
        for intf in interfaces_to_test:
            cli_interface_shutdown(intf)

        time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

        # Ensure the links go down
        for intf in interfaces_to_test:
            up = cli_interface_oper_status_up(intf)
            assert not up, '%s not down' % (intf)

        # startup ports
        for intf in interfaces_to_test:
            cli_interface_startup(intf)

        # Ensure the links are up
        t_limit = time.time() + timeout
        while time.time() < t_limit:
            time.sleep(_POLL_PERIOD_S)
            timeout -= _POLL_PERIOD_S
            all_up = True
            for intf in interfaces_to_test:
                up = cli_interface_oper_status_up(intf)
                if not up:
                    all_up = False
                    break
            if all_up:
                break
        if not all_up:
            # try to assert for the first offending port
            for intf in interfaces_to_test:
                up = cli_interface_oper_status_up(intf)
                assert up, '%s: not up after %fs' % (intf, timeout)

            # if that fails, just assert
            assert all_up, 'port(s) not up after %fs' % (timeout)

        # small extra delay to make sure LLDP table is updated(?)
        time.sleep(_DELAY_LLDP_UPDATE_S)

        # Ensure the ports appear in the LLDP table.
        for intf in interfaces_to_test:
            cmdstr  = cmd_lldp_table
            clistr  = cli_wrap(cmdstr)
            lines = clistr.splitlines()
            found = False
            for line in lines:
                if intf in line:
                    found = True
                    break
            assert found, '%s not in lldp table' % intf

    if _STRESS_TEST_LOOP_DBG:
        print('test_check_stress_link_status (%d ports, %d loops) done' % (num_interfaces, loopcnt))


'''
Following test cases from the plan are TBD:

    @summary: Restart xcvrd
    Confirm xcvrd restarts successfully without causing link flaps for corresponding 
    ports, and verify their presence in the LLDP table. 
    Also ensure that xcvrd is up for at least 2 mins

    @summary: Induce I2C errors and restart xcvrd
    Confirm xcvrd restarts successfully without causing link flaps for corresponding 
    ports, and verify their presence in the LLDP table

    @summary: Modify xcvrd.py to raise an Exception and induce a crash
    Confirm xcvrd restarts successfully without causing link flaps for corresponding 
    ports, and verify their presence in the LLDP table. 
    Also ensure that xcvrd is up for at least 2 mins

    @summary: Restart pmon
    Confirm xcvrd restarts successfully without causing link flaps for corresponding 
    ports, and verify their presence in the LLDP table

    @summary: Restart swss
    Ensure xcvrd restarts (for Mellanox platform, ensure pmon restarts) and expected 
    ports link up again, with port details visible in the LLDP table

    @summary: Restart syncd
    Ensure xcvrd restarts (for Mellanox platform, ensure pmon restarts) and expected 
    ports link up again, with port details visible in the LLDP table

    @summary: Perform a config reload
    Ensure xcvrd restarts and the expected ports link up again, with port details 
    visible in the LLDP table

    @summary: Execute a cold reboot
    Confirm the expected ports link up again post-reboot, with port details visible 
    in the LLDP table

    @summary: In a loop, execute cold reboot 100 times
    Confirm the expected ports link up again post-reboot, with port details visible 
    in the LLDP table

    @summary: Execute a warm reboot (if platform supports it)
    Ensure xcvrd restarts and maintains link stability for the interested ports, 
    with their presence confirmed in the LLDP table

    @summary: Execute a fast reboot (if platform supports it)
    Confirm the expected ports link up again post-reboot, with port details visible 
    in the LLDP table
'''



def test_the_link_status_tests():
    '''
    @summary: TEMPORARY test code: Run all tests in this file.
    '''
    print('test_the_link_status_tests BEGIN')
    util_wrapper_init()

    test_check_link_status(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_stress_link_status(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)


    print('test_the_link_status_tests END')


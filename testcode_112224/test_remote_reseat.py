'''Transceiver Onboarding Test - Remote Reseat

xcvr_onboarding_test_plan   sect. 1.5 Remote Reseat

'''
#import os
#import sys
import time

from sonic_platform.platform import Platform
import logging

from api_wrapper    import *   # wrappers for (optoe, sfp, sfp_base, xcvr_api, cmis, ...)
from cli_wrapper    import *   # wrappers for CLI
from util_wrapper   import *   # wrappers replacing platform_tests/sfp/util.py
from test_cfg       import *   # wrappers dealing with test config file etc.


# Local Constants
# wait times for port to power DOWN/UP (and DOM + status to be updated)
_DELAY_AFTER_IF_SHUTDOWN_S  = 3.0
_DELAY_AFTER_IF_RESET_S     = 5.0
_DELAY_AFTER_IF_LPMODE_ON_S = 3.0

_POLL_PERIOD_S              = 1.0
_MAX_WAIT_FOR_LINK_UP_S     = 60.0

# For coherent, time to wait after link up
_MAX_WAIT_FOR_LINK_UP_COHERENT_S = 180


# CLI commands
cmd_int_trans_reset     = 'sudo sfputil reset '
cmd_int_show_lpmode     = 'sudo sfputil show lpmode -p '
cmd_int_set_lpmode      = 'sudo sfputil lpmode on '
cmd_int_clr_lpmode      = 'sudo sfputil lpmode off '
cmd_lldp_table          = 'show lldp table '


def test_remote_reseat(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: 
    Issue CLI command to disable DOM monitoring            
    Issue CLI command to shutdown the port                 
    Reset the transceiver followed by a sleep for 5s       
    Put transceiver in low power mode (if LPM supported)   
    Put transceiver in high power mode (if LPM supported)  
    Issue CLI command to startup the port 	 	           
    Issue CLI command to enable DOM monitoring for the port
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check Remote Reseat")

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname   # ???
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # TBD: assert or allow partial test?
            #assert cli_interface_present(intf)
            if not cli_interface_present(intf):
                print('%s not present? skipping test' % (intf))
                continue

            # DOM disable and hence the test is N/A on subports > 1
            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_remote_reseat ', intf, ' Skipped (not main (sub)port)')
                continue

            # all subports related to intf
            portlist  = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            subports  = cli_interface_all_subports(intf, portlist, namespace)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # Issue CLI command to shutdown the port
                for sub in subports:
                    cli_interface_shutdown(sub)

                # wait for shutdown to complete
                time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

                # Ensure that the port is linked down
                up = cli_interface_oper_status_up(intf)
                assert not up

                # Reset the transceiver followed by a sleep for 5s       
                cmdstr = cmd_int_trans_reset + ' ' + intf
                resp = cli_wrap(cmdstr)

                # Ensure reset command executes successfully
                assert resp and 'OK' in resp, '%s failed' % (cmdstr)

                # wait for port to power down
                time.sleep(_DELAY_AFTER_IF_RESET_S)

                # Put transceiver in low power mode (if LPM supported)
                if switchname != 'Arista-7050CX3-32S-C32' and has_lpmode(intf):

                    cmdstr = cmd_int_set_lpmode + intf
                    resp = cli_wrap(cmdstr)
                    assert resp, '%s failed' % (cmdstr)

                    time.sleep(_DELAY_AFTER_IF_LPMODE_ON_S)

                    # Ensure that the port is in low power mode
                    cmdstr = cmd_int_show_lpmode + ' ' + intf
                    resp = cli_wrap(cmdstr)
                    lines = resp.splitlines()
                    line  = lines[2]
                    items = line.split()
                    assert items[1] == 'On', '%s not LPMode' % (intf)

                    # Put transceiver in high power mode (if LPM supported)  
                    cmdstr = cmd_int_clr_lpmode + intf
                    resp = cli_wrap(cmdstr)
                    assert resp, '%s failed' % (cmdstr)

                    # Ensure that the port is in high power mode
                    cmdstr = cmd_int_show_lpmode + ' ' + intf
                    resp = cli_wrap(cmdstr)
                    lines = resp.splitlines()
                    line  = lines[2]
                    items = line.split()
                    assert items[1] == 'Off', '%s in LPMode' % (intf)

                    # (no wait here; waiting below for link up)

                # Issue CLI command to startup the port
                for sub in subports:
                    cli_interface_startup(sub)

                # Ensure that the port is linked up and is seen in the LLDP table
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

                cmdstr = cmd_lldp_table
                clistr = cli_wrap_sh(cmdstr)
                lines = clistr.splitlines()
                found = False
                for line in lines:
                    items = line.split()
                    if len(items) == 5:
                        local_port  = items[0]
                        remote_port = items[4]
                        remote_id   = items[2]
                        if local_port == intf:
                            found = True
                            break
                assert found, '%s not in lldp table' % intf
            
            print('test_the_remote_reseat_tests ', intf, ' done') # TEMPORARY


def test_the_remote_reseat_tests():
    '''
    @summary: TEMPORARY test code: Run all tests in this file.
    '''
    print('test_the_remote_reseat_tests BEGIN')
    util_wrapper_init()

    test_remote_reseat(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    print('test_the_remote_reseat_tests END')

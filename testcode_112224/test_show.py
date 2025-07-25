'''Transceiver Onboarding Test - "show int trans xxxx" commands

xcvr_onboarding_test_plan   sect. 1.3 sfpshow commands

Only checking presence (cli_interface_present) in the first test. If only a 
subset of the tests is run, you may want to check presence in all the tests.
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
# wait time for port to power DOWN/UP (and DOM + status to be updated)
_DELAY_AFTER_IF_SHUTDOWN_S  = 3.0
#_DELAY_AFTER_IF_STARTUP_S   = 20.0 # CSCO 2x100G 5s, ARST 1x100G 8s, CSCO 400G 16-20s

# 09/10/24 Mihir wants polling loop 1s period/60s max for link up
_POLL_PERIOD_S              = 1.0
# replaces _DELAY_AFTER_IF_STARTUP_S
_MAX_WAIT_FOR_LINK_UP_S     = 60.0

# For coherent, time to wait after link up to ensure PM stats are updated
# TBD: how long? Seems to take about 45s(?)
# 10/17/24 [MP] 240s for Coherent
# 10/18/24 [MP] 240s "total link up time" for Coherent
_MAX_WAIT_FOR_LINK_UP_COHERENT_S = 180
#_DELAY_PM_UPDATE_S          = 48.0
_DELAY_PM_UPDATE_S          = 60.0


# DOM min/max
# Reasonable ranges expected in practice in these tests, not advertised limits.
_DOM_MIN_VCC        = 3.1   # [V]   (CMIS QSFP-DD HW: 3.135)
_DOM_MAX_VCC        = 3.5   # [V]   (CMIS QSFP-DD HW: 3.465)
_DOM_MIN_TEMP       = 5.0   # [C]
_DOM_MAX_TEMP       = 70.0

_DOM_MIN_TXBIAS_ON  = 1.0   # [mA]
_DOM_MAX_TXBIAS_ON  = 250.0
_DOM_TXBIAS_OFF     = 0.0

#_DOM_MIN_TXPWR_ON   = -8.0  # [dBm]
_DOM_MIN_TXPWR_ON   = -15.0  # [dBm] Coherent may be -8 to -10
_DOM_MAX_TXPWR_ON   = 5.0
_DOM_TXPWR_OFF      = -40.0
_DOM_MIN_RXPWR_ON   = _DOM_MIN_TXPWR_ON
_DOM_MAX_RXPWR_ON   = _DOM_MAX_TXPWR_ON
_DOM_RXPWR_OFF      = _DOM_TXPWR_OFF


# CLI commands
# Due to SONIC's slow and inconsistent polling, it can take forever for monitor
# values to get updated. Using sfputil (realtime) instead of "show" as workaround.
cmd_int_trans_info      = 'show interfaces transceiver info '
cmd_int_trans_dom       = 'sudo sfputil show eeprom -d -p '
#cmd_int_trans_dom       = 'show interfaces transceiver eeprom -d ' # slow
cmd_int_presence        = 'show interfaces transceiver presence '
cmd_int_status          = 'show interfaces status '
cmd_int_trans_error     = 'show interfaces transceiver error-status '
cmd_int_trans_error_hw  = 'show interfaces transceiver error-status -hw '
cmd_lldp_table          = 'show lldp table '
cmd_int_pm              = 'show interfaces transceiver pm '


def test_check_show_transceiver_info(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify transceiver specific information through CLI

    Test "show interfaces transceiver info", comparing output to test config file.
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_trans_info))

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

            cmdstr  = cmd_int_trans_info + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            clidict = cli_output2dict(clistr, delimiter=':')    # decode output
            assert len(clidict) > 8     # actually about 35 (for CMIS)

            if is_cmis(intf):
                assert port_cfg['active_firmware']  == clidict['Active Firmware']
                assert port_cfg['inactive_firmware']== clidict['Inactive Firmware']
                assert port_cfg['cmis_rev']         == clidict['CMIS Rev']

            assert port_cfg['vendor_date']  == clidict['Vendor Date Code(YYYY-MM-DD Lot)']
            assert port_cfg['vendor_name']  == clidict['Vendor Name']
            assert port_cfg['vendor_oui']   == clidict['Vendor OUI']
            assert port_cfg['vendor_pn']    == clidict['Vendor PN']
            assert port_cfg['vendor_rev']   == clidict['Vendor Rev']
            assert port_cfg['vendor_sn']    == clidict['Vendor SN']
            
            print('test_check_show_transceiver_info ', intf, ' done') # TEMPORARY DEBUG


def test_check_transceiver_DOM(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify DOM data is read correctly and is within an acceptable 
    range (if transceiver supports DOM). Ensure the fields are in line with 
    the expectation based on interface shutdown/no shutdown state.

        admin@sonic:~$ sudo sfputil show eeprom -d -p Ethernet0
        ..
        ChannelMonitorValues:
                RX1Power: -0.994dBm
                RX2Power: -0.904dBm
                RX3Power: -0.871dBm
                RX4Power: -0.868dBm
                TX1Bias: 7.24mA
                TX1Power: 0.144dBm
                TX2Bias: 7.232mA
                TX2Power: 0.137dBm
                TX3Bias: 7.24mA
                TX3Power: 0.144dBm
                TX4Bias: 7.232mA
                TX4Power: 0.137dBm
        ..
        ModuleMonitorValues:
                Temperature: 24.543C
                Vcc: 3.25Volts
        ..
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_trans_dom))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname   # ???
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            cmdstr  = cmd_int_trans_dom + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            clidict = cli_output2dict(clistr, delimiter=':')    # decode output
            assert len(clidict) > 8

            # ModuleMonitorValues
            #    Temperature: 24.543C   # assume range {1 : 69}
            #    Vcc: 3.25Volts         # HW limits ~ {3.135 : 3.465}
            if 'Temperature' in clidict:
                tmp = cli_parse_float_with_unit( clidict['Temperature'] )
                assert tmp and (tmp > _DOM_MIN_TEMP) and (tmp < _DOM_MAX_TEMP)

            if 'Vcc' in clidict:
                vcc = cli_parse_float_with_unit( clidict['Vcc'] )
                assert vcc and (vcc > _DOM_MIN_VCC) and (vcc < _DOM_MAX_VCC) 

            # ChannelMonitorValues - depend on port state (shut/no shut).
            # Only if txceiver is optical; CLI may incorrectly display power/bias 
            # for DACs where these are N/A.
            if is_optical(intf):

                # for breakout SONIC dumps all 8 lanes, try to find the relevant ones
                startlane,endlane = cli_interface_medialanes(intf, namespace)
                # here we want lanes 1-based for matching CLI output
                startlane += 1
                endlane += 1
                endlane += 1 # for use in range

                # (1) port enabled, assuming this is the default state
                # check TX<lane>Power
                for lane in range(startlane, endlane):
                    tgt = 'TX' + str(lane) + 'Power'
                    if tgt in clidict:
                        txpwr = cli_parse_float_with_unit( clidict[tgt] )
                        assert txpwr and (txpwr > _DOM_MIN_TXPWR_ON) and (txpwr < _DOM_MAX_TXPWR_ON)
                    else:
                        break
                
                # check TX<lane>Bias
                for lane in range(startlane, endlane):
                    tgt = 'TX' + str(lane) + 'Bias'
                    if tgt in clidict:
                        txbias = cli_parse_float_with_unit( clidict[tgt] )
                        assert txbias and (txbias > _DOM_MIN_TXBIAS_ON) and (txbias < _DOM_MAX_TXBIAS_ON)
                    else:
                        break
                
                # check RX<lane>Power - assuming other end is transmitting
                for lane in range(startlane, endlane):
                    tgt = 'RX' + str(lane) + 'Power'
                    if tgt in clidict:
                        rxpwr = cli_parse_float_with_unit( clidict[tgt] )
                        assert rxpwr and (rxpwr > _DOM_MIN_RXPWR_ON) and (rxpwr < _DOM_MAX_RXPWR_ON)
                    else:
                        break

                # While SFF does define LPMode it does not require that it disables Tx,
                # only that it reduce power to class 1 (1.5W) which is not verifiable. 
                if is_cmis(intf) and has_lpmode(intf):
                    # (2) port disabled
                    # disable port
                    cli_interface_shutdown(intf)

                    # wait for port to power down (and DOM to be updated)
                    time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)
                    # this check is mostly DEBUG:
                    up = cli_interface_oper_status_up(intf)
                    assert not up, '%s not down after %fs' % (intf, _DELAY_AFTER_IF_SHUTDOWN_S)
                    
                    cmdstr  = cmd_int_trans_dom + ' ' + intf
                    clistr  = cli_wrap(cmdstr)                          # run CLI command
                    clidict = cli_output2dict(clistr, delimiter=':')    # decode output
                    assert len(clidict) > 8
                    
                    # check TX<lane>Power
                    for lane in range(startlane, endlane):
                        tgt = 'TX' + str(lane) + 'Power'
                        if tgt in clidict:
                            txpwr = cli_parse_float_with_unit( clidict[tgt] )
                            
                            #if not txpwr: print('not txpwr')                          # TEMPORARY DEBUG
                            #elif not (txpwr == _DOM_TXPWR_OFF): print('txpwr=',txpwr) # TEMPORARY DEBUG
                            assert txpwr and (txpwr == _DOM_TXPWR_OFF)
                        else:
                            break
                    
                    # check TX<lane>Bias
                    for lane in range(startlane, endlane):
                        tgt = 'Tx' + str(lane) + 'Bias'
                        if tgt in clidict:
                            txbias = cli_parse_float_with_unit( clidict[tgt] )
                            assert txbias and (txbias == _DOM_TXBIAS_OFF)
                        else:
                            break
                    
                    # DON'T check RX<lane>Power again here. It wouldn't have changed;
                    # the other end wasn't shut down, and Rx power is reported even
                    # if a port is shut down.
                
                    # (3) re-enable port
                    cli_interface_startup(intf)
                
                    # wait for port to power up
                    # We COULD do this only once outside of this loop, but that wouldn't
                    # work for channelized ports. (E.g. if Ethernet0/Ethernet4 were part 
                    # of the same 2x100G physical port.)
                    #time.sleep(_DELAY_AFTER_IF_STARTUP_S)
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

            print('test_check_transceiver_dom ', intf, ' done') # TEMPORARY DEBUG



def test_check_transceiver_status(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify transceiver status when the interface is in shutdown 
    and no shutdown state.

    show interface presence <port>  - check module presence (redundant?)
    show interface status <port>    - check module state
    show lldp table                 - check that module shows up
    
    admin@sonic:~$ show int stat Ethernet0
      Interface    Lanes    Speed    MTU    FEC        Alias    Vlan    Oper    Admin                      Type    Asym PFC
    -----------  -------  -------  -----  -----  -----------  ------  ------  -------  ------------------------  ----------
      Ethernet0  1,2,3,4     100G   9100     rs  Ethernet1/1  routed      up       up  QSFP+ or later with CMIS         N/A
    
    admin@sonic:~$ show lldp table
    Capability codes: (R) Router, (B) Bridge, (O) Other
    LocalPort    RemoteDevice    RemotePortID       Capability    RemotePortDescr
    -----------  --------------  -----------------  ------------  -----------------
    Ethernet0    sonic           40:14:82:8a:16:00  BR            Ethernet0
    Ethernet4    sonic           40:14:82:8a:16:00  BR            Ethernet4
    ..

    admin@sonic:~$ sudo config int shut Ethernet0

    admin@sonic:~$ show int stat Ethernet0
      Interface    Lanes    Speed    MTU    FEC        Alias    Vlan    Oper    Admin                      Type    Asym PFC
    -----------  -------  -------  -----  -----  -----------  ------  ------  -------  ------------------------  ----------
      Ethernet0  1,2,3,4     100G   9100     rs  Ethernet1/1  routed    down     down  QSFP+ or later with CMIS         N/A

    admin@sonic:~$ show lldp table
    Capability codes: (R) Router, (B) Bridge, (O) Other
    LocalPort    RemoteDevice    RemotePortID       Capability    RemotePortDescr
    -----------  --------------  -----------------  ------------  -----------------
    Ethernet4    sonic           40:14:82:8a:16:00  BR            Ethernet4
    ..
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_status))
    logging.info("        and, of '{}'".format(cmd_lldp_table))
    logging.info("        and, of '{}'".format(cmd_int_presence)) # ?

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname   # ???
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # (1) port enabled, assuming this is the default state

            # (1a) interface status
            cmdstr  = cmd_int_status + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            line  = lines[2]
            items = line.split()
            assert items[0] == intf, '%s not in status' % intf
            assert items[7] == 'up', '%s wrong oper  state %s' % (intf, items[7])
            # wrong admin state would be a SONIC bug, not a txceiver issue
            #assert items[8] == 'up', '%s wrong admin state %s' % (intf, items[8])

            # (1b) lldp table
            cmdstr  = cmd_lldp_table
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            found = False
            for line in lines:
                if intf in line:
                    found = True
                    break
            assert found, '%s not in lldp table' % intf

            # (1c) presence
            cmdstr  = cmd_int_presence + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            line  = lines[2]
            items = line.split()
            assert items[0] == intf, '%s not in status' % intf
            assert items[1] == 'Present', '%s not Present' % (intf)

            # (2) port disabled

            # disable port
            cli_interface_shutdown(intf)

            # wait for port to power down (and status to be updated)
            time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

            # (2a) interface status
            cmdstr  = cmd_int_status + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            line  = lines[2]
            items = line.split()
            assert items[0] == intf, '%s not in status' % intf
            assert items[7] == 'down', '%s wrong oper  state %s' % (intf, items[7])
            # wrong admin state would be a SONIC bug, not a txceiver issue
            #assert items[8] == 'down', '%s wrong admin state %s' % (intf, items[8])

            # (2b) lldp table
            cmdstr  = cmd_lldp_table
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            found = False
            for line in lines:
                if intf in line:
                    found = True
                    break
            assert not found, '%s in lldp table' % intf

            # (2c) no point checking presence again; not affected by shutdown

            # (3) re-enable port
            cli_interface_startup(intf)

            # wait for port to power up
            # We COULD do this only once outside of this loop, but that wouldn't
            # work for channelized ports. (E.g. if Ethernet0/Ethernet4 were part 
            # of the same 2x100G physical port.)
            #time.sleep(_DELAY_AFTER_IF_STARTUP_S)
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

            print('test_check_transceiver_status ', intf, ' done') # TEMPORARY DEBUG


def test_check_transceiver_C_CMIS(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify PM information (for C-CMIS transceivers).

    admin@sonic:~$ show int trans pm Ethernet0
    Ethernet0: Transceiver performance monitoring not applicable

    admin@sonic:~$ show interfaces transceiver pm Ethernet128
    Ethernet128:
        Parameter        Unit    Min       Avg       Max       Threshold    ..
                                                               High         ..
                                                               Alarm        
        ---------------  ------  --------  --------  --------  -----------  ..
        Tx Power         dBm     -40.0     -40.0     -40.0     -4.0         ..
        Rx Total Power   dBm     -40.0     -39.98    -28.53    N/A          ..
        Rx Signal Power  dBm     -40.0     -39.98    -28.53    N/A          ..
        CD-short link    ps/nm   0.0       0.0       0.0       N/A          ..
        PDL              dB      0.0       0.0       0.0       N/A          ..
        OSNR             dB      0.0       0.0       0.0       N/A          ..
        eSNR             dB      0.0       0.0       0.0       N/A          ..
        CFO              MHz     0.0       0.0       0.0       N/A          ..
        DGD              ps      0.0       0.0       0.0       N/A          ..
        SOPMD            ps^2    0.0       0.0       0.0       N/A          ..
        SOP ROC          krad/s  0.0       0.0       0.0       N/A          ..
        Pre-FEC BER      N/A     1.00E+00  1.00E+00  1.00E+00  N/A          ..
        Post-FEC BER     N/A     0.0       0.0       0.0       N/A          ..
        EVM              %       0.0       0.0       0.0       N/A          ..
    admin@sonic:~$ 
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_pm))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    # flag to mkae sure we only do an initial wait once (not for each module)
    waited_for_update = False
    
    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            #if not is_cmis(intf):
            #    print('test_check_transceiver_C_CMIS ', intf, ' Skipped (not CMIS)') # (?)
            #    continue
            # is_coherent() also checks is_cmis()
            if not is_coherent(intf):
                print('test_check_transceiver_C_CMIS ', intf, ' Skipped (not CMIS/Coherent)')
                continue

            # Even if the previous test ensured links are up, we need to wait
            # here to ensure PM stats are updated.
            # But at least we don't need to wait for each txceiver, one wait should do it.
            if not waited_for_update:
                print('waiting %us for PM update' % (_DELAY_PM_UPDATE_S)) # TEMPORARY DEBUG(?)
                time.sleep(_DELAY_PM_UPDATE_S)
                waited_for_update = True

            cmdstr = cmd_int_pm + ' ' + intf
            clistr = cli_wrap(cmdstr)
            lines = clistr.splitlines()
            if 'not applicable' in lines[0]:
                print('test_check_transceiver_C_CMIS ', intf, ' Skipped (N/A)')
                continue

            # Param name is 1-3 words, so offset of 'Min' etc. varies:
            min_offs = [0,0,0,0,0, 3,4,4,3, 2,2,2,2,2,2, 3,3,3,2]  # (0=invalid)

            for i in range(5, 18):
                line  = lines[i]
                items = line.split()
                param_name = ''
                #print('DBG:liine: ', line) # TEMPORARY DEBUG
                try:
                    for pn in range(0, min_offs[i] - 1):
                        param_name += items[pn] + ' '
                except:
                    assert False, 'Failed to parse Parameter name'

                try:
                    #Min = float(items[min_offs[i] + 0])
                    Avg = float(items[min_offs[i] + 1])
                    #Max = float(items[min_offs[i] + 2])
                except:
                    assert False, 'Failed to parse float for %s' % (param_name)

                # check if values seem (roughly) valid ("-" = no check)
                # Parameter        Unit     >=  (min)   <=  (max)
                # ---------------  ------   ---------   ---------
                # Tx Power         dBm      -15         -
                # Rx Total Power   dBm      -15         -      
                # Rx Signal Power  dBm      -15         -      
                # CD-short link    ps/nm    -32000      32000
                # PDL              dB       0           100
                # OSNR             dB       0           100
                # eSNR             dB       0           100
                # CFO              MHz      5000        -5000
                # DGD              ps       0           1000
                # SOPMD            ps^2     0           1000
                # SOP ROC          krad/s   0           1000
                # Pre-FEC BER      N/A      0.0         1.0
                # Post-FEC BER     N/A      0.0         1.0
                # EVM              %        0           -      
                if i <= 7:      # Tx/Rx power
                    assert Avg >= _DOM_MIN_TXPWR_ON, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i == 8:    # CD
                    assert Avg >= -32000 and Avg <= 32000, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i <= 11:   # PDL,SNR
                    assert Avg >= 0     and Avg <= 100, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i == 12:   # CFO
                    assert Avg >= -5000  and Avg <= 5000, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i <= 15:   # DGD,SOP
                    assert Avg >= 0     and Avg <= 1000, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i <= 17:   # BER
                    assert Avg >= 0.0   and Avg <= 1.0, '%s %s invalid Avg %f' % (intf, param_name, Avg)
                elif i == 18:   # EVM [%]
                    assert Avg >= 0, '%s %s invalid Avg %f' % (intf, param_name, Avg)

            print('test_check_transceiver_C_CMIS ', intf, ' done') # TEMPORARY DEBUG


def test_check_transceiver_VDM(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify VDM information Verify VDM information for CMIS cables.

    Validate CLI relying on redis-db

    Ensure that all the Pre-FEC and FERC media and host related VDM related
    fields are populated. The acceptable values for Pre-FEC fields are from 
    0 through 1e-4 and the FERC values should be <= 0
    '''

    # TBD (maybe a bit early; not much VDM support in SONIC or txceivers yet?)
    # Are there even any VDM CLI commands ?

    print('test_check_transceiver_VDM', ': TBD') # TEMPORARY DEBUG
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_???))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            if not is_cmis(intf):
                print('test_check_transceiver_VDM ', intf, ' Skipped (not CMIS)') # (?)
                continue




            print('test_check_transceiver_VDM ', intf, ' done') # TEMPORARY DEBUG
    '''



def test_check_transceiver_error_status(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify transceiver error-status
     - Verify transceiver error-status, relying on redis-db.
     - Verify transceiver error-status with hardware verification, relying on
        transceiver hardware.
    Ensure the relevant port is in an "OK" state.

    "show int transceiver error-status <port>"
    "show int transceiver error-status -hw <port>"

    admin@sonic:~$ show int trans error-status Ethernet0
    Port       Error Status
    ---------  --------------
    Ethernet0  OK

    admin@sonic:~$ show int trans error-status -hw Ethernet0
    Port       Error Status
    ---------  --------------
    Ethernet0  OK
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_trans_error))
    logging.info("        and, of '{}'".format(cmd_int_trans_error_hw))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # (1) error status relying on DB
            cmdstr  = cmd_int_trans_error + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            items = lines[2].split()
            assert items[0] == intf, '%s not in error-status' % intf
            assert items[1] == 'OK', '%s error-status %s' % (intf, items[1])

            # (2) error status relying on HW
            cmdstr  = cmd_int_trans_error_hw + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            lines = clistr.splitlines()
            items = lines[2].split()
            assert items[0] == intf, '%s not in error-status' % intf
            assert items[1] == 'OK', '%s error-status %s' % (intf, items[1])

            print('test_check_transceiver_error_status ', intf, ' done') # TEMPORARY DEBUG


def test_the_tests():
    '''
    @summary: TEMPORARY test code: Run all tests in this file.
    '''
    print('test_the_tests BEGIN')
    util_wrapper_init()

    test_check_show_transceiver_info(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_transceiver_DOM(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_transceiver_status(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_transceiver_C_CMIS(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_transceiver_VDM(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_transceiver_error_status(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    print('test_the_tests END')

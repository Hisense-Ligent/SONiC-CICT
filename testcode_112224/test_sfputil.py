'''Transceiver Onboarding Test - "sfputil commands"

xcvr_onboarding_test_plan   sect. 1.2 sfputil commands
The following tests aim to validate various functionalities of the transceiver 
using the sfputil command.

Only checking presence (test_check_sfputil_transceiver_presence) in the first 
test. If only a subset of the tests is run, you may want to check presence in 
all the tests (e.g. using cli_interface_present).

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
#_DELAY_AFTER_IF_STARTUP_S   = 20.0  # CSCO 2x100G 5s, ARST 1x100G 8s, CSCO 400G 16-20s
_DELAY_AFTER_IF_RESET_S     = 5.0   # 09/10/24 Mihir wants 5s (was 2s)
_DELAY_AFTER_IF_LPMODE_ON_S = 3.0
#_DELAY_AFTER_IF_LPMODE_OFF_S= 20.0  # CSCO 2x100G 5s, ARST N/A, CSCO 400G 16-20s

# small extra delay for LLDP table update after link up(?)
# 10/17/24/MP: 180s
_DELAY_LLDP_UPDATE_S        = 1.0

# 09/10/24 use polling loop, 1s period/60s max for link up
_POLL_PERIOD_S              = 1.0
# replaces _DELAY_AFTER_IF_STARTUP_S, _DELAY_AFTER_IF_LPMODE_OFF_S
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
cmd_int_trans_pres      = 'sudo sfputil show presence -p '
cmd_int_trans_reset     = 'sudo sfputil reset '
cmd_int_trans_dom       = 'sudo sfputil show eeprom -d -p '
cmd_int_show_eeprom     = 'sudo sfputil show eeprom -p '
cmd_int_show_eeprom_hex = 'sudo sfputil show eeprom-hexdump -p ' # --page <page>
cmd_int_show_lpmode     = 'sudo sfputil show lpmode -p '
cmd_int_set_lpmode      = 'sudo sfputil lpmode on '
cmd_int_clr_lpmode      = 'sudo sfputil lpmode off '
cmd_int_show_fwver      = 'sudo sfputil show fwversion '
cmd_lldp_table          = 'show lldp table '


#----------------------------------------------------------------------------
# Local utility functions
#----------------------------------------------------------------------------

# CMIS loopback types (page 0x13 byte 128 bitmask)
LB_NONE         = 0x00
LB_MEDIA_OUTPUT = 0x01 # media output looped back to input (inwards towards txceiver)
LB_MEDIA_INPUT  = 0x02 # media input looped back to output (outward towards fiber)
LB_HOST_OUTPUT  = 0x04 # host output looped back to input  (inwards towards txceiver)
LB_HOST_INPUT   = 0x08 # host input looped back to output  (outward towards host)
# CMIS loopback type cmd substrings
STR_LB_NONE         = 'none'
STR_LB_MEDIA_OUTPUT = 'media-side-output'
STR_LB_MEDIA_INPUT  = 'media-side-input'
STR_LB_HOST_OUTPUT  = 'host-side-output'
STR_LB_HOST_INPUT   = 'host-side-input'

LOOPTYPE_STRINGS = {
    LB_NONE         : STR_LB_NONE,
    LB_MEDIA_OUTPUT : STR_LB_MEDIA_OUTPUT,
    LB_MEDIA_INPUT  : STR_LB_MEDIA_INPUT,
    LB_HOST_OUTPUT  : STR_LB_HOST_OUTPUT,
    LB_HOST_INPUT   : STR_LB_HOST_INPUT,
}

def testutil_support_diags(intf, namespace):
    '''Return True if txceiver supports diag pages 0x13-0x14 (page 1 byte 142 bit 5)
    '''
    rc = False

    cmdstr = cmd_int_show_eeprom_hex + ' ' + intf + ' --page 0x01'
    clistr = cli_wrap_sh(cmdstr)
    lines = clistr.splitlines()
    try:
        line = lines[12+10] 
        item = line.split()[15] # byte 0x8e(142)
        val = int(item, 16)
        if val & 0x20:
            rc = True
    except:
        rc = False

    return rc

def testutil_supported_loopbacks(intf, namespace):
    '''Return bitmask of supported loopback types.
    
    Ref.: CMIS 5.0 Fig.8-3 & Table 8-89.
    '''
    loops = 0

    if testutil_support_diags(intf, namespace):
        cmdstr = cmd_int_show_eeprom_hex + ' ' + intf + ' --page 0x13'  
        clistr = cli_wrap_sh(cmdstr)
        lines = clistr.splitlines()

        try:
            line = lines[12+10] 
            item = line.split()[1]  # byte 0x80(128)
            val = int(item, 16)
            loops = val & 0x0f      # ignoring per-lane and simultaneous bits
        except:
            pass

    return loops


#----------------------------------------------------------------------------
# Tests
#----------------------------------------------------------------------------

def test_check_sfputil_transceiver_presence(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify if transceiver presence works with CLI

    sudo sfputil show presence -p <port>

    admin@sonic:~$ sudo sfputil show presence -p Ethernet4
    Port       Presence
    ---------  ----------
    Ethernet4  Present
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_trans_pres))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # presence
            cmdstr  = cmd_int_trans_pres + ' ' + intf
            clistr  = cli_wrap(cmdstr)
            lines = clistr.splitlines()
            line  = lines[2]
            items = line.split()
            assert items[0] == intf, '%s not in status' % (intf)
            assert items[1] == 'Present', '%s not Present' % (intf)

            print('test_check_sfputil_transceiver_presence ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_reset(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Reset the transceiver followed by issuing shutdown and then startup command

    Ensure that the port is linked down after reset and is in low power mode (if 
    transceiver supports it). 
    Also, ensure the DataPath is in DPDeactivated state and LowPwrAllowRequestHW 
    (page 0h, byte 26.6) is set to 1. 
    The shutdown and startup commands are later issued to re-initialize the port 
    and bring the link up.

    sudo sfputil reset <port>

    N/A on Arista
    [MP:] Ideally, the reset behavior is to reset the module. However, we had 
    earlier found that the Arista-7050 SKU has LowPwrRequestHW pin always asserted 
    which causes the module to not go through the rest. We can create an exception 
    for this test for the Arista-7050 specific SKU since the intention here is to 
    catch both module as well as platforms which do not exhibit the expected reset 
    behavior.

    Breakout PORTS (e.g. 200G switch port split into two 100G sub-ports) would be 
    a special case; both sub-ports would go down in Reset or LPMode.
    [MP:] You can for now focus on handling single ports (non-breakout ports) and 
    we can extend the support for breakout ports later.
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check '{}'".format(cmd_int_trans_reset))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # all subports related to intf
            portlist  = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            subports  = cli_interface_all_subports(intf, portlist, namespace)

            # reset
            cmdstr = cmd_int_trans_reset + ' ' + intf
            resp = cli_wrap(cmdstr)
            assert resp, '%s failed' % (cmdstr)
            # wait for port to power down
            time.sleep(_DELAY_AFTER_IF_RESET_S)

            if switchname != 'Arista-7050CX3-32S-C32' :
                # Ensure port is linked down
                up = cli_interface_oper_status_up(intf)
                assert not up, '%s not down after %fs' % (intf, _DELAY_AFTER_IF_RESET_S)
                
                # Ensure port is in LPMode (if supported)
                if has_lpmode(intf):
                    #admin@sonic:~$ sudo sfputil show lpmode -p Ethernet0
                    #Port       Low-power Mode
                    #---------  ----------------
                    #Ethernet0  Off
                    cmdstr = cmd_int_show_lpmode + ' ' + intf
                    resp = cli_wrap(cmdstr)
                    lines = resp.splitlines()
                    line  = lines[2]
                    items = line.split()
                    assert items[0] == intf, '%s not in status' % intf
                    assert items[1] == 'On', '%s not LPMode' % (intf)

                if is_cmis(intf):
                    # Ensure datapaths are DPDeactivated (1)
                    base_cmdstr = cmd_int_show_eeprom_hex + ' ' + intf + ' '
                    cmdstr  = base_cmdstr + '--page ' + '0x11'  # dump page 0x11
                    clistr  = cli_wrap(cmdstr)
                    lines = clistr.splitlines()
                    
                    # DP states - should be 1 = DPDeactivated
                    # New SONIC version always shows upper page 0 as well...
                    #items = lines[12].split()[1:5]
                    items = lines[12+10].split()[1:5]
                    states= []
                    for x in items:
                        # first byte lowest two DP
                        states.append(int(x[1],16)) # bits 3:0 low DP
                        states.append(int(x[0],16)) # bits 7:4 high DP
                    target = 1                      # should be 1 = DPDeactivated
                    startlane,endlane = cli_interface_hostlanes(intf, namespace)
                    endlane += 1 # for use in range
                    for i in range (startlane,endlane):
                        assert states[i] == target, 'DP %d state %x != %x' % (i, states[i], target)

                    # Ensure LowPwrAllowRequestHW (byte 26 bit6) is set after reset 
                    item = lines[3].split()[11]
                    val = int(item, 16)
                    assert val & 0x40, 'LowPwrAllowRequestHW not set'

            # shutdown/startup all subports related to intf
            # shutdown
            for sub in subports:
                cli_interface_shutdown(sub)

            # wait for shutdown to complete
            time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)
            
            # startup
            for sub in subports:
                cli_interface_startup(sub)

            # Ensure link up for all subports
            timeout = _MAX_WAIT_FOR_LINK_UP_S
            if is_coherent(intf):
                timeout = _MAX_WAIT_FOR_LINK_UP_COHERENT_S
            t_limit = time.time() + timeout
            while time.time() < t_limit:
                time.sleep(_POLL_PERIOD_S)
                timeout -= _POLL_PERIOD_S
                all_up = True
                for sub in subports:
                    up = cli_interface_oper_status_up(sub)
                    if not up:
                        all_up = False
                        break
                if all_up:
                    break
            assert all_up, '%s: %s not up after %fs' % (intf, sub, timeout)

            # (no need to check that lpmode is off; link up implies lpmode off)

            print('test_check_sfputil_transceiver_reset ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_lpmode(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Put transceiver in low power mode (if transceiver supports it) 
    followed by restoring to high power mode.

    Ensure that the port is linked down and datapaths are DPDeactivated after
    putting the transceiver in low power mode. Ensure that the port is in low 
    power mode through CLI. Disable low power mode and ensure link is up.

    sudo sfputil lpmode on/off <port>

    Breakout PORTS (e.g. 200G switch port split into two 100G sub-ports) would be 
    a special case; both sub-ports would go down in Reset or LPMode.
    [MP:] You can for now focus on handling single ports (non-breakout ports) and 
    we can extend the support for breakout ports later.
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check LPMode")

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            portlist    = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf)
            assert port_cfg

            # all subports related to intf
            subports  = cli_interface_all_subports(intf, portlist, namespace)

            # check if LPMode supported, otherwise skip this port
            if not has_lpmode(intf):
                logging.info("Skip LPMode test, %s does not support LPMode" % (intf))
                print("Skip LPMode test, %s does not support LPMode" % (intf))
                continue

            # While SFF does define LPMode it does not require that it disables Tx,
            # only that it reduce power to class 1 (1.5W) which is not verifiable. 
            if not is_cmis(intf):
                logging.info("Skip LPMode test, %s non-CMIS does not require TxDis on LPMode" % (intf))
                print("Skip LPMode test, %s non-CMIS does not require TxDis on LPMode" % (intf))
                continue

            # set LPMode
            cmdstr = cmd_int_set_lpmode + intf
            resp = cli_wrap(cmdstr)
            assert resp, '%s failed' % (cmdstr)

            time.sleep(_DELAY_AFTER_IF_LPMODE_ON_S)

            # Ensure link is down
            #up = cli_interface_oper_status_up(intf)
            #assert not up, '%s not down after %fs' % (intf, _DELAY_AFTER_IF_LPMODE_ON_S)
            # check all subports related to intf?
            for sub in subports:
                up = cli_interface_oper_status_up(sub)
                assert not up, '%s: %s not down after %fs' % (intf, sub, _DELAY_AFTER_IF_LPMODE_ON_S)

            if is_cmis(intf):
                # Ensure datapaths are DPDeactivated (1)
                # Use sfputil hexdump command here because it's realtime
                base_cmdstr = cmd_int_show_eeprom_hex + ' ' + intf + ' '
                cmdstr  = base_cmdstr + '--page ' + '0x11'  # dump page 0x11
                clistr  = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                
                # DP states - should be 1 = DPDeactivated
                #10/03/24: New SONIC version always shows upper page 0 as well...
                #items = lines[12].split()[1:5]
                items = lines[12+10].split()[1:5]
                states= []
                for x in items:
                    # first byte lowest two DP
                    states.append(int(x[1],16)) # bits 3:0 low DP
                    states.append(int(x[0],16)) # bits 7:4 high DP
                target = 1                      # should be 1 = DPDeactivated
                startlane,endlane = cli_interface_hostlanes(intf, namespace)
                endlane += 1 # for use in range
                for i in range (startlane,endlane):
                    assert states[i] == target, 'DP %d state %x != %x' % (i, states[i], target)

            # clear LPMode
            cmdstr = cmd_int_clr_lpmode + intf
            resp = cli_wrap(cmdstr)
            assert resp, '%s failed' % (cmdstr)

            # Ensure link up for all subports
            timeout = _MAX_WAIT_FOR_LINK_UP_S
            if is_coherent(intf):
                timeout = _MAX_WAIT_FOR_LINK_UP_COHERENT_S
            t_limit = time.time() + timeout
            while time.time() < t_limit:
                time.sleep(_POLL_PERIOD_S)
                timeout -= _POLL_PERIOD_S
                all_up = True
                for sub in subports:
                    up = cli_interface_oper_status_up(sub)
                    if not up:
                        all_up = False
                        break
                if all_up:
                    break
            assert all_up, '%s: %s not up after %fs' % (intf, sub, timeout)

            # (no need to check lpmode; link up implies lpmode is off)

            print('test_check_sfputil_transceiver_lpmode ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_eeprom(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify EEPROM of the transceiver using CLI
    
    Ensure transceiver specific fields are matching with the values retrieved 
    from transceiver_static_info.yaml file.

    Duplicate of sect. 1.3 "Verify transceiver specific information through CLI"
    except that here we're using sfputil which reads directly instead of going
    through the DB --AND has a lot less info. 
    So, this is more about testing SONIC than testing transceivers...

    admin@sonic:~$ sudo sfputil show eeprom -p Ethernet0
    Ethernet0: SFP EEPROM detected
            Application Advertisement: {1: {'host_electrical_interface_id': 'CAUI-4 C2M (Annex 83E)', 'module_media_interface_id': 'Active Cable assembly with BER < 5x10^-5', 'media_lane_count': 4, 'host_lane_count': 4, 'host_lane_assignment_options': 1, 'media_lane_assignment_options': 1}}
            Connector: No separable connector
            Encoding: N/A
            Extended Identifier: Power Class 2 (2.5W Max)
            Extended RateSelect Compliance: N/A
            Identifier: QSFP+ or later with CMIS
            Length Cable Assembly(m): 2.0
            Nominal Bit Rate(100Mbs): 0
            Specification compliance:
                    N/A
            Vendor Date Code(YYYY-MM-DD Lot): 2023-11-20
            Vendor Name: Hisense
            Vendor OUI: ac-4a-fe
            Vendor PN: DEF8504-2C02-MB3
            Vendor Rev: 02
            Vendor SN: VEMDBT0200A-A
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_show_eeprom))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            cmdstr  = cmd_int_show_eeprom + ' ' + intf
            clistr  = cli_wrap(cmdstr)                          # run CLI command
            clidict = cli_output2dict(clistr, delimiter=':')    # decode output
            assert len(clidict) > 8     # actually about 35 (for CMIS)

            assert port_cfg['vendor_date']  == clidict['Vendor Date Code(YYYY-MM-DD Lot)']
            assert port_cfg['vendor_name']  == clidict['Vendor Name']
            assert port_cfg['vendor_oui']   == clidict['Vendor OUI']
            assert port_cfg['vendor_pn']    == clidict['Vendor PN']
            assert port_cfg['vendor_rev']   == clidict['Vendor Rev']
            assert port_cfg['vendor_sn']    == clidict['Vendor SN']

            print('test_check_sfputil_transceiver_eeprom ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_dom(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify DOM information of the transceiver using CLI when interface is 
    in shutdown and no shutdown state (if transceiver supports DOM),

    Ensure the fields are in line with the expectation based on interface shutdown/no 
    shutdown state,

    sudo sfputil show eeprom -d -p

    Duplicate of sect. 1.3 "Verify DOM data is read correctly ..." except that here 
    we're using sfputil which reads directly instead of going through the DB. 
    So, this is more about testing SONIC than testing transceivers...
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
            switchname  = duthost.hostname
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
                if is_cmis(intf):
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
                    
                            #if not txbias:
                            #    print('not txbias')                        # TEMPORARY DEBUG
                            #    print('tgt=%s' % (tgt))
                            #elif not (txbias == _DOM_TXBIAS_OFF): print('txbias=',txbias) # TEMPORARY DEBUG
                    
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

            print('test_check_sfputil_transceiver_dom ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_eeprom_hexdump(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify EEPROM hexdump of the transceiver using CLI
    
    Ensure the output shows Lower Page (0h) and Upper Page (0h) for all 128 bytes on each page. 
    Information from transceiver_static_info.yaml can be used to validate contents of page 0h. 
    Also, ensure that page 11h shows the Data Path state correctly

    Again, more a test of SONIC than of transceivers...
    
    admin@sonic:~$ sudo sfputil show eeprom-hexdump -p Ethernet0
    EEPROM hexdump for port Ethernet0 page 0h
            Lower page 0h
            00000000 1e 52 04 06 01 00 00 00  00 00 00 00 00 00 1a 33 |.R.............3|
            00000010 7e e4 00 00 00 00 00 00  00 00 40 00 00 00 00 00 |~.........@.....|
            00000020 00 00 f0 00 ff 01 00 00  05 00 00 00 00 00 00 00 |................|
            00000030 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000040 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000050 00 00 00 00 00 04 0b 02  44 01 ff 00 00 00 00 00 |........D.......|
            00000060 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000070 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
    
            Upper page 0h
            00000080 1e 48 69 73 65 6e 73 65  20 20 20 20 20 20 20 20 |.Hisense        |
            00000090 20 ac 4a fe 44 45 46 38  35 30 34 2d 32 43 30 32 | .J.DEF8504-2C02|
            000000a0 2d 4d 42 33 30 32 56 45  4d 44 42 54 30 32 30 30 |-MB302VEMDBT0200|
            000000b0 41 2d 41 20 20 20 32 33  31 31 32 30 20 20 20 20 |A-A   231120    |
            000000c0 20 20 20 20 20 20 20 20  20 0a 42 23 00 00 00 00 |         .B#....|
            000000d0 00 00 f0 03 00 00 00 00  00 00 00 00 00 00 b4 00 |................|
            000000e0 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            000000f0 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
    
    admin@sonic:~$ sudo sfputil show eeprom-hexdump -p Ethernet0 --page 0x11
    EEPROM hexdump for port Ethernet0 page 0x11h
            Lower page 0h
            00000000 1e 52 04 06 01 00 00 00  00 00 00 00 00 00 1a 3e |.R.............>|
            00000010 7e ea 00 00 00 00 00 00  00 00 40 00 00 00 00 00 |~.........@.....|
            00000020 00 00 f0 00 ff 01 00 00  05 00 00 00 00 00 00 00 |................|
            00000030 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000040 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000050 00 00 00 00 00 04 0b 02  44 01 ff 00 00 00 00 00 |........D.......|
            00000060 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
            00000070 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|
    
            Upper page 0x11h
            00000080 44 44 11 11 0f 0f 0f 00  00 00 00 00 00 00 00 00 |DD..............|
            00000090 00 00 00 00 00 00 00 00  00 0f 28 44 28 38 28 44 |..........(D(8(D|
            000000a0 28 38 00 00 00 00 00 00  00 00 0e 56 0e 52 0e 56 |(8.........V.R.V|
            000000b0 0e 52 00 00 00 00 00 00  00 00 1f 29 1f 68 1f fe |.R.........).h..|
            000000c0 1f fe 00 00 00 00 00 00  00 00 00 00 00 00 10 10 |................|
            000000d0 10 10 00 00 00 00 00 00  00 22 22 00 00 0f 0f 00 |........."".....|
            000000e0 00 00 00 00 00 00 00 11  11 00 00 00 00 00 00 00 |................|
            000000f0 00 00 00 00 00 00 00 00  00 00 00 00 00 00 00 00 |................|

    10/03/24: New SONIC version always shows upper page 0 as well
    So the above no longer matches.

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
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            base_cmdstr = cmd_int_show_eeprom_hex + ' ' + intf + ' '

            if is_cmis(intf):
                # page 0
                cmdstr  = base_cmdstr + '--page ' + '0'
                clistr  = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                
                # CMIS rev "52" -> "5.2"
                items = lines[2].split()
                s = items[2][0] + '.' + items[2][1]
                assert port_cfg['cmis_rev'] == s
                
                # vendor name "48 69 73 65 6e 73 65  20 20..." -> "Hisense"
                items1 = lines[12].split()
                items2 = lines[13].split()
                s = ''
                for x in items1[2:17]:
                    s += chr(int(x,16))
                x = items2[1]
                s += chr(int(x,16))
                assert port_cfg['vendor_name'] == s.strip() 
                
                # vendor PN "44 45 46 38 35 30 34 2d 32 43 30 32" -> "DEF8504-2C02"
                items1 = lines[13].split()[5:17]
                items2 = lines[14].split()[1:5]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_pn'] == s.strip() 
                
                # vendor SN
                items1 = lines[14].split()[7:17]
                items2 = lines[15].split()[1:7]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_sn'] == s.strip() 
                
                # vendor date
                # EEPROM "32 33 31 31 32 30" -> port_cfg format '2023-11-20'
                items1 = lines[15].split()[7:13]
                s = ''
                for x in items1: s += chr(int(x,16))
                s2 = '20' + s[0:2] + '-' + s[2:4] + '-' + s[4:6]
                assert port_cfg['vendor_date'] == s2
                
                # page 0x11
                cmdstr  = base_cmdstr + '--page ' + '0x11'
                clistr  = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                
                # DP states - should be 4 = Activated
                #10/03/24: New SONIC version always shows upper page 0 as well...
                #items = lines[12].split()[1:5]
                items = lines[12+10].split()[1:5]
                states= []
                for x in items:
                    # first byte lowest two DP
                    states.append(int(x[1],16)) # bits 3:0 low DP
                    states.append(int(x[0],16)) # bits 7:4 high DP
                target = 4
                startlane,endlane = cli_interface_hostlanes(intf, namespace)
                endlane += 1 # for use in range
                for i in range (startlane,endlane):
                    assert states[i] == target, 'DP %d state %x != %x' % (i, states[i], target)

            elif is_sff8436(intf) or is_sff8636(intf):
                # page 0
                cmdstr  = base_cmdstr + '--page ' + '0'
                clistr  = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                assert len(lines) >= 20

                # vendor name
                items1 = lines[13].split()[5:17]
                items2 = lines[14].split()[1:5]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_name'] == s.strip() 

                # vendor PN
                items1 = lines[14].split()[9:17]
                items2 = lines[15].split()[1:9]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_pn'] == s.strip() 

                # vendor SN
                items1 = lines[16].split()[5:17]
                items2 = lines[17].split()[1:5]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_sn'] == s.strip() 

                # vendor date
                # EEPROM "32 33 31 31 32 30" -> port_cfg format '2023-11-20'
                items1 = lines[17].split()[5:13]
                s = ''
                for x in items1: s += chr(int(x,16))
                s2 = '20' + s[0:2] + '-' + s[2:4] + '-' + s[4:6]
                assert port_cfg['vendor_date'] == s2

            elif is_sff8472(intf):
                # page 0
                cmdstr  = base_cmdstr + '--page ' + '0'
                clistr  = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                assert len(lines) >= 10

                # 20-35 vendor name
                items1 = lines[3].split()[5:17]
                items2 = lines[4].split()[1:5]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_name'] == s.strip() 

                # 40-55 vendor PN
                items1 = lines[4].split()[9:17]
                items2 = lines[5].split()[1:9]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_pn'] == s.strip() 

                # 68-83 vendor SN
                items1 = lines[6].split()[5:17]
                items2 = lines[7].split()[1:5]
                s = ''
                for x in items1: s += chr(int(x,16))
                for x in items2: s += chr(int(x,16))
                assert port_cfg['vendor_sn'] == s.strip() 

                # 84-91 vendor date
                # EEPROM "32 33 31 31 32 30" -> port_cfg format '2023-11-20'
                items1 = lines[6].split()[5:13]
                s = ''
                for x in items1: s += chr(int(x,16))
                s2 = '20' + s[0:2] + '-' + s[2:4] + '-' + s[4:6]
                assert port_cfg['vendor_date'] == s2

            else:
                print('test_check_sfputil_transceiver_eeprom_hexdump ', intf, ' Skipped (unsupported type)')

            print('test_check_sfputil_transceiver_eeprom_hexdump ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_fw_version(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify firmware version of the transceiver using CLI (requires disabling DOM config)
    
    Ensure the active and inactive firmware version is in line with the expectation from 
    transceiver_static_info.yaml

        admin@sonic:~$ sudo sfputil show fwversion Ethernet4
        Image A Version: 0.5.0
        Image B Version: N/A
        Factory Image Version: 0.0.0
        Running Image: A
        Committed Image: A
        Active Firmware: 0.5.0
        Inactive Firmware: 9.3.0

    Enable/disable DOM monitoring for a port:
        admin@sonic:~$ sudo config interface -n '' trans dom Ethernet0 enable
        admin@sonic:~$
        admin@sonic:~$ sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet0" "dom_polling"
        enabled
        admin@sonic:~$ sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet4" "dom_polling"

    NOTE: For breakout, always issue this command on the FIRST subport within 
        the breakout port group!

    Verification of DOM monitoring enable/disable:
        sonic-db-cli -n '<namespace>' CONFIG_DB hget "PORT|<port>" "dom_polling"
    Expected output:
        For enable: "dom_polling" = "enabled" or "(nil)"
        For disable: "dom_polling" = "disabled"

    To check breakout and subport number:
        sonic-db-cli -n '<NAMESPACE>' CONFIG_DB hget "PORT|<port>" "subport"
        sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet0" "subport"
        sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet4" "subport"
    Expected output:
        non-breakout ports: 0 or (nil)
        breakout ports:     1 .. N, subport = 1 being the first subport
    Example, Cisco (has subports):
        admin@sonic:~$ sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet0" "subport"
        1
        admin@sonic:~$ sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet4" "subport"
        2
    Example, Arista (NO subports):
        admin@sonic:~$ sonic-db-cli -n '' CONFIG_DB hget "PORT|Ethernet0" "subport"
        
        admin@sonic:~$
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check output of '{}'".format(cmd_int_show_fwver))

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            if not is_cmis(intf):
                # N/A in the other older protocols
                print('test_check_sfputil_transceiver_fw_version ', intf, ' Skipped (not CMIS)')
                continue

            # For breakout cables/ports, disable DOM polling on FIRST subport.
            # To that end we need to find that first subport (the port itself 
            # if no breakout).

            # get list of test ports
            yyy = test_cfg_read()
            assert test_cfg_valid(yyy)
            intf_list = test_cfg_ports(yyy, switchname, namespace=namespace)
            assert intf_list and len(intf_list) > 1 and intf in intf_list

            # find first subport corresponding to <intf> in test port list
            first_intf = cli_interface_first_subport(intf, intf_list, namespace)
            assert first_intf , 'FIRST subport not found for %s' % (intf)

            # disable DOM, using context manager
            with cli_dom_disabled(first_intf, namespace):
                # now run the command we're supposed to test on <intf> itself
                cmdstr  = cmd_int_show_fwver + ' ' + intf
                clistr  = cli_wrap(cmdstr)
                clidict = cli_output2dict(clistr, delimiter=':')
                
                assert 'Active Firmware' in clidict
                assert 'Inactive Firmware' in clidict
                assert port_cfg['active_firmware']  == clidict['Active Firmware']
                assert port_cfg['inactive_firmware']== clidict['Inactive Firmware']

            print('test_check_sfputil_transceiver_fw_version ', intf, ' done') # TEMPORARY DEBUG


def test_check_sfputil_transceiver_loopback(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Verify different types of loopback

    sudo sfputil debug loopback <port> <loopback_type>
        [none|host-side-input|host-side-output|media-side-input|media-side-output]

    Ensure that the various supported types of loopback work on the transceiver. 
    The LLDP neighbor can also be used to verify the data path after enabling loopback.

    $ show lldp table
    LocalPort    RemoteDevice  RemotePortID       Capability  RemotePortDescr
    -----------  ------------  -----------------  ----------  ---------------
    Ethernet0    sonic         cc:1a:a3:91:b9:78  BR          Ethernet0
    ..

    10/04/24/Mihir: "On production network, the remote ID will be the alias name 
                    of the port and not the mac ID."
    So, we cannot use RemotePortId to check. We only check if the local port is 
    present in the LLDP table. (Which doesn't tell us if there's a loop or not.)
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check loopback")

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    # cannot use RemotePortId (see function header comment)
    #chassis_mac = cli_chassis_mac(namespace)
    #assert chassis_mac and len(chassis_mac) >= 17

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg

            # only test for "main ports" (?)
            subport = cli_interface_subport(intf, namespace)
            if subport and subport > 1:
                print('test_check_sfputil_transceiver_loopback ', intf, ' Skipped (subport)')
                continue
    
            if not is_cmis(intf):
                print('test_check_sfputil_transceiver_loopback ', intf, ' Skipped (not CMIS)')
                continue

            # which loops to test
            # For "local" testing (running in switch), only loopbacks towards
            # this switch (LB_MEDIA_OUTPUT, LB_HOST_INPUT) can be verified.
            #loops_to_test = [LB_MEDIA_OUTPUT, LB_HOST_INPUT]
            loops_to_test = [LB_MEDIA_OUTPUT]
            # limit list to those supported
            loops_supported = testutil_supported_loopbacks(intf, namespace) # bitmask
            for l in loops_to_test:
                if not l & loops_supported:
                    loops_to_test.remove(l)

            if not loops_to_test:
                print('test_check_sfputil_transceiver_loopback ', intf, ' Skipped (N/A)')
                continue

            for looptype in loops_to_test:
                # Set up loop.
                #   $ sudo sfputil debug loopback Ethernet0 media-side-output
                #   Ethernet0: Set media-side-output loopback
                #
                #   $ sudo sfputil debug loopback Ethernet0 media-side-output
                #   Ethernet0: Set media-side-output loopback failed
                typestr = LOOPTYPE_STRINGS[looptype]
                cmdstr = 'sudo sfputil debug loopback ' + intf + ' ' + typestr
                clistr = cli_wrap_sh(cmdstr)
                assert clistr and not 'fail' in clistr.lower()
                
                # check (wait for) link up
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

                # TBD: do we need a small extra delay here to make sure LLDP table is updated?
                time.sleep(_DELAY_LLDP_UPDATE_S)

                # check LLDP table - should be local == remote
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
                            # cannot use RemotePortId (see function header comment)
                            # Remote ID and remote port name should both match local.
                            #assert remote_id == chassis_mac and remote_port == intf
                            break
                assert found, '%s not in lldp table' % intf
                
                # clear loop
                cmdstr = 'sudo sfputil debug loopback ' + intf + ' none'
                clistr = cli_wrap_sh(cmdstr)
                assert clistr and not 'fail' in clistr.lower() and 'set none' in clistr.lower()
                
                # check (wait for) link up
                timeout = _MAX_WAIT_FOR_LINK_UP_S
                if is_coherent(intf):
                    timeout = _MAX_WAIT_FOR_LINK_UP_COHERENT_S
                up = False
                t_limit = time.time() + timeout
                while time.time() < t_limit:
                    time.sleep(_POLL_PERIOD_S)
                    timeout -= _POLL_PERIOD_S
                    up = cli_interface_oper_status_up(intf)
                    if up:
                        break
                assert up, '%s not up after %fs' % (intf, timeout)

                # small extra delay here to make sure LLDP table is updated(?)
                time.sleep(_DELAY_LLDP_UPDATE_S)

                # check LLDP table - should be local != remote
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
                            # cannot use RemotePortId (see function header comment)
                            # Remote ID and remote port name shouldn't both match local.
                            # Either one may match, but not both.
                            #assert remote_id != chassis_mac or remote_port != intf
                            break
                assert found, '%s not in lldp table' % intf

            print('test_check_sfputil_transceiver_fw_loopback ', intf, ' done') # TEMPORARY DEBUG



def test_the_sfputil_tests():
    '''
    @summary: TEMPORARY test code: Run all tests in this file.
    '''
    print('test_the_tests BEGIN')
    util_wrapper_init()

    test_check_sfputil_transceiver_presence(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_reset(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_lpmode(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_eeprom(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_dom(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_eeprom_hexdump(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_fw_version(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_check_sfputil_transceiver_loopback(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    print('test_the_tests END')

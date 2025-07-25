'''Transceiver Onboarding Test - Firmware Related Tests

xcvr_onboarding_test_plan   sect. 1.4 Firmware Related Tests

All the firmware related tests assume that the DOM monitoring is disabled for
the corresponding port and the output of dmesg is analyzed to ensure that no 
I2C errors are seen during firmware related testing.

Only checking presence (cli_interface_present) in the first test. If only a 
subset of the tests is run, you may want to check presence in all the tests.

'''
import os
import sys
import time
#import signal,subprocess

from sonic_platform.platform import Platform
import logging

from api_wrapper    import *   # wrappers for (optoe, sfp, sfp_base, xcvr_api, cmis, ...)
from cli_wrapper    import *   # wrappers for CLI
from util_wrapper   import *   # wrappers replacing platform_tests/sfp/util.py
from test_cfg       import *   # wrappers dealing with test config file etc.


# Local Constants
_DELAY_AFTER_IF_SHUTDOWN_S  = 3.0
_DELAY_AFTER_IF_STARTUP_S   = 20.0  # (?) CSCO 2x100G 5s, ARST 1x100G 8s, CSCO 400G 16-20s
_DELAY_AFTER_IF_RESET_S     = 5.0
_DELAY_AFTER_IF_LPMODE_ON_S = 3.0
_MAX_TIME_DOWNLOAD_S        = 30*60.0
_DELAY_DOWNLOAD_KILL_S      = 20    # time to wait before killing download

# CLI commands
cmd_int_trans_reset     = 'sudo sfputil reset '
cmd_dmesg_i2c_err       = 'sudo dmesg | grep -iE "error|fail|warning" | grep optoe'
cmd_fw_download         = 'sudo sfputil firmware download '
cmd_fw_run              = 'sudo sfputil firmware run '
cmd_fw_commit           = 'sudo sfputil firmware commit '

cmd_int_set_lpmode      = 'sudo sfputil lpmode on '
cmd_int_clr_lpmode      = 'sudo sfputil lpmode off '
cmd_int_show_lpmode     = 'sudo sfputil show lpmode -p '


# Record of normal dowlnoad times per switch/port.
# Values to be inserted by test_download_valid_fw.
# Used by test_download_abort to determine the times at which to abort.
DownloadTimes = dict()


def test_download_invalid_fw(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Download INvalid firmware

    Ensure that the active firmware version does not change.
    Ensure that the inactive version either does not change or changes to 0.0.0.
    (Inactive version may (a) stay the same in case of wrong image (for different 
    txceiver) or (b) change to 0.0.0 in case of right but corrupted image.
    
    Ensure no link flap is seen during this process.
    
    sudo sfputil firmware download <port> <fwfile>
    
    Note: Do not attempt if transceiver doesn't have dual-bank support!
    '''
    logging.info("Check Download of invalid firmware")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname

            # Do not attempt if not dual-bank support!
            # Downloading an invalid img might brick the txceiver depending
            # on what exactly is invalid about the image.
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg
            if not port_cfg['dual_bank_support']:
                print('test_download_invalid_fw ', intf, ' Skipped (not dual bank)')
                continue

            # TBD: assert or allow partial test?
            #assert cli_interface_present(intf)
            if not cli_interface_present(intf):
                print('%s not present? skipping test' % (intf))
                continue

            if not is_cmis(intf):
                print('test_download_invalid_fw ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_invalid_fw ', intf, ' Skipped (not main (sub)port)')
                continue

            # download image path
            img_path = test_cfg_fw_img_path(test_cfg, switchname, intf, invalid=True, namespace=namespace)
            if not img_path:
                # don't assert; not all txceivers may have download files
                print('test_download_invalid_fw ', intf, ' Skipped (no FW image)')
                continue
            # on the other hand, if it's specified it should be there
            assert os.path.isfile(img_path)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)
                assert orig_active and orig_active == port_cfg['active_firmware']
                assert orig_inactive and orig_inactive == port_cfg['inactive_firmware']

                # get original link flap count
                orig_flaps = cli_link_flap_count(intf, namespace)

                print('DBG test_download_invalid_fw ', intf, ' download start') # TEMPORARY DEBUG

                # Do the download
                cmdstr = cmd_fw_download  + ' ' + intf + ' ' + img_path
                #print('cmdstr: \n', cmdstr)                                    # TEMPORARY DEBUG
                clistr = cli_wrap_sh(cmdstr)
                #print('clistr: \n', clistr)                                    # TEMPORARY DEBUG

                # Exact type of failure may depend on how the img is invalid.
                # cli_wrap returns None in case of an error code received.
                assert clistr == None or 'fail' in clistr.lower()

                print('DBG test_download_invalid_fw ', intf, ' download end')   # TEMPORARY DEBUG

                # get current FW versions, check if they changed
                curr_active, curr_inactive = cli_fw_version(intf)
                # Active version should not change.
                assert curr_active and curr_active == orig_active
                # Inactive version may (a) stay the same in case of a wrong image 
                # (for different txceiver) or (b) change to 0.0.0 in case of the
                # right but corrupted image.
                assert curr_inactive 
                assert curr_inactive == orig_inactive or curr_inactive == '0.0.0'

                # ensure no link flap is seen
                # just current state, not the history, will miss short blips
                up = cli_interface_admin_status_up(intf)
                assert up, 'link not up'
                # actual flap count
                curr_flaps = cli_link_flap_count(intf, namespace)
                assert curr_flaps == orig_flaps, '%u link flaps' % (curr_flaps - orig_flaps)

            print('test_download_invalid_fw ', intf, ' done') # TEMPORARY DEBUG


def test_download_valid_fw(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Download valid firmware

    admin@sonic:~$ sudo sfputil firmware download Ethernet32 <img path>

    Look for a “Firmware download complete success” message to confirm the 
    firmware is downloaded successfully. 
    Also, a return code of 0 will denote CLI executed successfully. (???)
    Ensure the inactive firmware version matches the downloaded firmware.
    Ensure no link flap is seen.
    Ensure that no I2C error is seen. (cmd_dmesg_i2c_err)
    Ensure that the firmware download time is less than 30 minutes

    Note: Do not attempt if transceiver doesn't have dual-bank support(?)
          Even if the image is valid, download might fail for other reasons.
    '''
    logging.info("Check Download of valid firmware")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname

            # Do not attempt if not dual-bank support(?)
            # Even if the image is valid, download might fail for other
            # reasons, potentially bricking the txceiver.
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg
            if not port_cfg['dual_bank_support']:
                print('test_download_valid_fw ', intf, ' Skipped (not dual bank)')
                continue

            if not is_cmis(intf):
                print('test_download_valid_fw ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_valid_fw ', intf, ' Skipped (not main (sub)port)')
                continue

            # version of download image (optional, don't assert if it's not there)
            img_ver = test_cfg_fw_img_ver(test_cfg, switchname, intf, namespace=namespace)

            # download image path
            img_path = test_cfg_fw_img_path(test_cfg, switchname, intf, invalid=False, namespace=namespace)
            if not img_path:
                # don't assert; not all txceivers may have download files
                print('test_download_valid_fw ', intf, ' Skipped (no FW image)')
                continue
            # on the other hand, if it's specified it should be there
            assert os.path.isfile(img_path)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions (inactive may have been wiped out by invalid img test)
                orig_active, orig_inactive = cli_fw_version(intf)
                assert orig_active and orig_active == port_cfg['active_firmware']
                assert orig_inactive and orig_inactive == port_cfg['inactive_firmware'] or orig_inactive=='0.0.0'

                # get original link flap count
                orig_flaps = cli_link_flap_count(intf, namespace)
    
                # get original I2C error (line) counts for later comparison
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                orig_i2c_errors = len(clistr.splitlines())

                print('DBG test_download_valid_fw ', intf, ' download start')  # TEMPORARY DEBUG

                # Record the start time
                t_start = time.time()

                # Do the download
                cmdstr = cmd_fw_download  + ' ' + intf + ' ' + img_path
                clistr = cli_wrap_sh(cmdstr)
                # expect something like this on success:
                #   CDB: Starting firmware download
                #   Downloading ...  [###################################-]   99%  00:00:00
                #   CDB: firmware download complete
                #   Firmware download complete success
                #   Total download Time: 0:17:11.060937
                assert clistr
                assert 'firmware download complete'         in clistr.lower()
                assert 'firmware download complete success' in clistr.lower()

                # Record the stop time
                t_stop = time.time()

                print('DBG test_download_valid_fw ', intf, ' download end')     # TEMPORARY DEBUG
                #print('DBG clistr: \n', clistr, '\n')                               # TEMPORARY DEBUG

                # Ensure the inactive firmware version matches the downloaded firmware.
                # get current FW versions, check if they changed
                curr_active, curr_inactive = cli_fw_version(intf)
                # active should be unchanged
                assert curr_active and curr_active == orig_active
                # inactive should match cfg (if listed there)
                if img_ver:
                    assert curr_inactive and curr_inactive == img_ver
    
                # ensure no link flap is seen
                # "show interface status" just gives the current state, not the
                #  history, so it will miss short blips
                up = cli_interface_admin_status_up(intf)
                assert up, 'link not up'
                #
                curr_flaps = cli_link_flap_count(intf, namespace)
                assert curr_flaps == orig_flaps, '%u link flaps' % (curr_flaps - orig_flaps)
    
                # Ensure that no I2C error is seen.
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                curr_i2c_errors = len(clistr.splitlines())
                new_i2c_errors = curr_i2c_errors - orig_i2c_errors
                assert new_i2c_errors == 0
    
                # Ensure that the firmware download time is less than 30 minutes
                t_elapsed = t_stop - t_start
                assert t_elapsed <= _MAX_TIME_DOWNLOAD_S

                # On success, record the download time for later use by abort test.
                DownloadTimes[switchname,intf] = t_elapsed

            print('test_download_valid_fw ', intf, ' done') # TEMPORARY DEBUG


def test_download_kill(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                        enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Kill the process which is downloading the firmware after firmware 
    download is triggered.

    Ensure that the active firmware version does not change and that the inactive
    version is 0.0.0.

    Ensure no link flap is seen.
    '''
    logging.info("Check process kill during Download")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname = duthost.hostname

            # Do not attempt if not dual-bank support!
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg
            if not port_cfg['dual_bank_support']:
                print('test_download_kill ', intf, ' Skipped (not dual bank)')
                continue

            if not is_cmis(intf):
                print('test_download_kill ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_kill ', intf, ' Skipped (not main (sub)port)')
                continue

            img_path = test_cfg_fw_img_path(test_cfg, switchname, intf, invalid=False, namespace=namespace)
            if not img_path:
                # don't assert; not all txceivers may have download files
                print('test_download_kill ', intf, ' Skipped (no FW image)')
                continue
            # on the other hand, if the file is specified it should be there
            assert os.path.isfile(img_path)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)

                # get original link flap count
                orig_flaps = cli_link_flap_count(intf, namespace)

                # start download
                cmdstr = cmd_fw_download  + ' ' + intf + ' ' + img_path
                p = cli_proc_spawn(cmdstr)
                assert cli_proc_running(p) , 'failed to start download process'
                # (after this, don't assert until after p.kill)

                print('waiting %ds before killing download..' % (_DELAY_DOWNLOAD_KILL_S)) # TEMPORARY
                time.sleep(_DELAY_DOWNLOAD_KILL_S)

                # kill the download process
                cli_proc_kill(p)
                time.sleep(0.5)
                assert not cli_proc_running(p) , 'failed to kill download process'

                # get and check FW versions
                curr_active, curr_inactive = cli_fw_version(intf)
                #print('killed download ', intf, ' FW act/inact=', curr_active,'/',curr_inactive) # TEMPORARY
                assert curr_active and curr_active == orig_active
                assert curr_inactive and curr_inactive == '0.0.0'

                # ensure no link flap is seen
                up = cli_interface_admin_status_up(intf)
                assert up, 'link not up'
                #
                curr_flaps = cli_link_flap_count(intf, namespace)
                assert curr_flaps == orig_flaps, '%u link flaps' % (curr_flaps - orig_flaps)

            print('test_download_kill ', intf, ' done') # TEMPORARY DEBUG


def test_download_abort(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                        enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    Download firmware and abort the firmware download at various intervals 
    (20%, 40%, 60%, 80% and 98% completion).
    
    Ensure that the inactive firmware version is 0.0.0.

    Abort the same way as in the "kill" test.

    For "percentage done", we can't capture the "%" output that' constantly 
    overwritten. It does not show up in stdout. Instead, we use a global dict
    with <switchname,portname> as keys and total download time as values. 
    Here, we're assuming this was populated by test_download_valid_fw.
    '''
    logging.info("Check process abort during Download")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname = duthost.hostname

            # Get total download time (needed to figure out when to abort)
            if not (switchname,intf) in DownloadTimes:
                print('test_download_abort ', intf, ' Skipped (no reference download time)')
                continue
            t_base = DownloadTimes[switchname,intf]
            assert t_base > 100, 'Invalid total download time %u recorded?' % (t_base)

            # Do not attempt if not dual-bank support!
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf, namespace=namespace)
            assert port_cfg
            if not port_cfg['dual_bank_support']:
                print('test_download_abort ', intf, ' Skipped (not dual bank)')
                continue

            if not is_cmis(intf):
                print('test_download_abort ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_abort ', intf, ' Skipped (not main (sub)port)')
                continue

            img_path = test_cfg_fw_img_path(test_cfg, switchname, intf, invalid=False, namespace=namespace)
            if not img_path:
                # don't assert; not all txceivers may have download files
                print('test_download_abort ', intf, ' Skipped (no FW image)')
                continue
            # on the other hand, if the file is specified it should be there
            assert os.path.isfile(img_path)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)

                # for each percentage
                for percentage in (20, 40, 60, 80, 98):
                    t_abort = t_base * percentage / 100.0

                    print('test_download_abort(%d%%)... ' % (percentage)) # TEMPORARY(?)

                    # start download
                    cmdstr = cmd_fw_download  + ' ' + intf + ' ' + img_path
                    p = cli_proc_spawn(cmdstr)
                    assert cli_proc_running(p) , 'failed to start download process'
                    # (after this, don't assert until after p.kill)

                    print('waiting %ds before aborting download..' % (t_abort)) # TEMPORARY
                    time.sleep(t_abort)

                    # kill the download process
                    cli_proc_kill(p)
                    time.sleep(0.5)
                    assert not cli_proc_running(p) , 'failed to kill download process'

                    # get and check FW versions
                    curr_active, curr_inactive = cli_fw_version(intf)
                    print('aborted download ', intf, ' FW act/inact=', curr_active,'/',curr_inactive) # TEMPORARY
                    assert curr_active and curr_active == orig_active
                    assert curr_inactive and curr_inactive == '0.0.0'

                    print('test_download_abort(%d%%) done ' % (percentage)) # TEMPORARY(?)

            print('test_download_abort ', intf, ' done') # TEMPORARY


def test_download_lpmode(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                        enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    Put transceiver in low power mode (if LPM supported) and perform firmware download

    Ensure that the port is in low power mode and the firmware download is successful. 
    Ensure that the active and inactive firmware versions are inline with expectation. 
    Revert the port to high power mode after the test
    '''
    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)

    logging.info("Check download in LPMode")

    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)
    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            portlist    = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            port_cfg = test_cfg_portcfg(test_cfg, switchname, intf)
            assert port_cfg

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_lpmode ', intf, ' Skipped (not main (sub)port)')
                continue

            # check if LPMode supported, otherwise skip this port
            if not has_lpmode(intf):
                logging.info("Skip LPMode test, %s does not support LPMode" % (intf))
                print("Skip LPMode test, %s does not support LPMode" % (intf))
                continue

            # get original FW versions
            orig_active, orig_inactive = cli_fw_version(intf)

            # version of download image
            img_ver = test_cfg_fw_img_ver(test_cfg, switchname, intf, namespace=namespace)

            # download image path
            img_path = test_cfg_fw_img_path(test_cfg, switchname, intf, invalid=False, namespace=namespace)
            if not img_path:
                print('test_download_lpmode ', intf, ' Skipped (no FW image)')
                continue
            assert os.path.isfile(img_path)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # set LPMode
                cmdstr = cmd_int_set_lpmode + intf
                resp = cli_wrap(cmdstr)
                assert resp, '%s failed' % (cmdstr)
                time.sleep(_DELAY_AFTER_IF_LPMODE_ON_S)

                # Ensure the port is in low power mode 
                cmdstr = cmd_int_show_lpmode + intf
                clistr = cli_wrap(cmdstr)
                lines = clistr.splitlines()
                assert intf in lines[2] and 'On' in lines[2]

                print('DBG test_download_lpmode ', intf, ' download start')  # TEMPORARY DEBUG

                # download
                cmdstr = cmd_fw_download  + ' ' + intf + ' ' + img_path
                clistr = cli_wrap_sh(cmdstr)

                print('DBG test_download_lpmode ', intf, ' download end')  # TEMPORARY DEBUG

                # Ensure the firmware download is successful. 
                assert 'firmware download complete'         in clistr.lower()
                assert 'firmware download complete success' in clistr.lower()

                # Ensure active and inactive firmware versions are as expected 
                curr_active, curr_inactive = cli_fw_version(intf)
                # active should be unchanged
                assert curr_active and curr_active == orig_active
                # inactive should match cfg (if listed there)
                if img_ver:
                    assert curr_inactive and curr_inactive == img_ver

                # Revert the port to high power mode after the test
                cmdstr = cmd_int_clr_lpmode + intf
                resp = cli_wrap(cmdstr)
                assert resp, '%s failed' % (cmdstr)

                # probably no need to wait for link up here; subsequent tests are
                # only concerned with download functionality(?)

            print('test_download_lpmode ', intf, ' done') # TEMPORARY DEBUG


def test_download_reset(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                        enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Execute transceiver reset after firmware download

    Ensure that the active and inactive firmware versions do not change. 

    Ensure that the link goes down after reset. - Seems redundant and irrelevant?
    Then perform interface shutdown followed by startup to bring the link up.
    
    Partly N/A on Arista; Arista doesn't keep the module in reset, so we can't
    reliably check link down. Which seems redundant and irrelevant anyway; it's
    already checked in the sfputil tests.)

    Requires that a (valid) download was done right before. Here we're ASSUMING
    that was done, so we do NOT need to do another.
    '''
    logging.info("Check reset after Download")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname = duthost.hostname
            portlist = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            subports = cli_interface_all_subports(intf, portlist, namespace)

            if not is_cmis(intf):
                print('test_download_reset ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_reset ', intf, ' Skipped (not main (sub)port)')
                continue

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)
    
                # assuming download already done
    
                # reset
                cmdstr = cmd_int_trans_reset + ' ' + intf
                resp = cli_wrap(cmdstr)
                assert resp, '%s failed' % (cmdstr)
                # wait for port to power down
                time.sleep(_DELAY_AFTER_IF_RESET_S)
    
                if switchname != 'Arista-7050CX3-32S-C32' :
                    # Ensure port is linked down
                    up = cli_interface_oper_status_up(intf)
                    assert not up, '%s not down' % (intf)
    
                # shutdown/startup all subports related to intf
                for sub in subports:
                    cli_interface_shutdown(sub)
                time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)
                for sub in subports:
                    cli_interface_startup(sub)
    
                # get current FW versions, check if they changed
                curr_active, curr_inactive = cli_fw_version(intf)
                assert curr_active and curr_active == orig_active
                assert curr_inactive and curr_inactive == orig_inactive

            print('test_download_reset ', intf, ' done') # TEMPORARY DEBUG


def test_download_run(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Execute firmware run command (with port in shutdown state)
    
    Look for a “Firmware run in mode=0 success” message to confirm the firmware
    is successfully running. Also, a return code of 0 will denote CLI executed 
    successfully. 
    With the firmware version dump CLI, ensure “Active Firmware” shows the new 
    firmware version. 
    Ensure that no I2C error is seen.

    Requires that a (valid) download was done right before. Here we're ASSUMING
    that was done, so we do NOT need to do another.
    '''
    logging.info("Check Run after Download")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            portlist = test_cfg_ports(test_cfg, switchname, namespace=namespace)

            if not is_cmis(intf):
                print('test_download_run ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_run ', intf, ' Skipped (not main (sub)port)')
                continue

            #shutdown port (and subports)
            subports = cli_interface_all_subports(intf, portlist, namespace)
            for sub in subports:
                cli_interface_shutdown(sub)
            time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)
    
                # get original I2C error (line) counts for later comparison
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                orig_i2c_errors = len(clistr.splitlines())
    
                # issue run command
                #   admin@sonic:~$ sudo sfputil firmware run Ethernet32
                #   Running firmware: Non-hitless Reset to Inactive Image
                #   Firmware run in mode=0 success
                cmdstr = cmd_fw_run + intf
                clistr = cli_wrap_sh(cmdstr)
                assert 'firmware run in mode=0 success' in clistr.lower()
    
                # get current FW versions, check if they changed
                curr_active, curr_inactive = cli_fw_version(intf)
                assert curr_active   and curr_active == orig_inactive # swapped
                assert curr_inactive and curr_inactive == orig_active # swapped
    
                # Ensure that no I2C error is seen.
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                curr_i2c_errors = len(clistr.splitlines())
                new_i2c_errors = curr_i2c_errors - orig_i2c_errors
                assert new_i2c_errors == 0
    
                # startup port (and subports)
                for sub in subports:
                    cli_interface_shutdown(sub)
                # here we don't really care if links come up, but wait long enough
                # that they'll most likely be up before any following test that 
                # might expect links up
                time.sleep(_DELAY_AFTER_IF_STARTUP_S)

            print('test_download_run ', intf, ' done') # TEMPORARY DEBUG


def test_download_commit(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Execute firmware commit command (with port in shutdown state)
    
    Look for a “Firmware commit successful” message. Please do not proceed 
    further if this message is not seen.

    With the firmware version dump CLI, ensure the “Committed Image” field is 
    updated with the relevant bank. 
    
    Ensure that no I2C error is seen.
    '''
    logging.info("Check Download Commit")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname  = duthost.hostname
            portlist = test_cfg_ports(test_cfg, switchname, namespace=namespace)

            if not is_cmis(intf):
                print('test_download_commit ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_commit ', intf, ' Skipped (not main (sub)port)')
                continue

            #shutdown port (and subports)
            subports = cli_interface_all_subports(intf, portlist, namespace)
            for sub in subports:
                cli_interface_shutdown(sub)
            time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original committed bank (and corresponding version)
                orig_bank, orig_ver = cli_committed_fw_bank_ver(intf)
    
                # get original I2C error (line) counts for later comparison
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                orig_i2c_errors = len(clistr.splitlines())
    
                # do the commit
                #   admin@sonic:~$ sfputil firmware commit Ethernet180
                #   Firmware commit successful
                cmdstr = cmd_fw_commit + intf
                clistr = cli_wrap_sh(cmdstr)
                assert 'firmware commit successful' in clistr.lower()
    
                # get current committed bank
                curr_bank, curr_ver = cli_committed_fw_bank_ver(intf)
                assert curr_bank and curr_bank != orig_bank
                # no guarantee that the two banks don't contain the same version
                #assert curr_ver and curr_ver != orig_ver
    
                # Ensure that no I2C error is seen.
                #clistr = cli_wrap_sh(cmd_dmesg_i2c_err)
                clistr = cli_wrap_sh_grep(cmd_dmesg_i2c_err)
                curr_i2c_errors = len(clistr.splitlines())
                new_i2c_errors = curr_i2c_errors - orig_i2c_errors
                assert new_i2c_errors == 0
    
                # startup port (and subports)
                for sub in subports:
                    cli_interface_shutdown(sub)
                # here we don't really care if links come up, but wait long enough
                # that they'll most likely be up before any following test that 
                # might expect links up
                time.sleep(_DELAY_AFTER_IF_STARTUP_S)

            print('test_download_commit ', intf, ' done') # TEMPORARY DEBUG


def test_download_post_run_reset(duthosts, enum_rand_one_per_hwsku_frontend_hostname,
                            enum_frontend_asic_index, conn_graph_facts, xcvr_skip_list):
    ''' 
    @summary: Execute transceiver reset post firmware run

    Ensure the active and inactive firmware versions are the same as what was 
    captured before initiating the firmware run.

    Execute interface shutdown followed by startup to recover the link. (I.e., 
    to clear the reset.)
    '''
    logging.info("Check reset after Run")

    duthost = duthosts[enum_rand_one_per_hwsku_frontend_hostname]
    global ans_host
    ans_host = duthost
    portmap, dev_conn = get_dev_conn(duthost, conn_graph_facts, enum_frontend_asic_index)
    namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'

    for intf in dev_conn:
        if intf not in xcvr_skip_list[duthost.hostname]:
            switchname = duthost.hostname
            portlist = test_cfg_ports(test_cfg, switchname, namespace=namespace)
            subports = cli_interface_all_subports(intf, portlist, namespace)

            if not is_cmis(intf):
                print('test_download_post_run_reset ', intf, ' Skipped (not CMIS)')
                continue

            sub_port = cli_interface_subport(intf, namespace)
            # 0 = no breakout, 1 = first subport
            if not (sub_port == 0 or sub_port == 1):
                print('test_download_post_run_reset ', intf, ' Skipped (not main (sub)port)')
                continue

            # keep DOM disabled during test
            with cli_dom_disabled(intf, namespace):

                # get original FW versions
                orig_active, orig_inactive = cli_fw_version(intf)
    
                # reset
                cmdstr = cmd_int_trans_reset + ' ' + intf
                resp = cli_wrap(cmdstr)
                assert resp, '%s failed' % (cmdstr)
                # wait for port to power down
                time.sleep(_DELAY_AFTER_IF_RESET_S)
    
                # get current FW versions, check if they changed
                curr_active, curr_inactive = cli_fw_version(intf)
                assert curr_active and curr_active == orig_active
                assert curr_inactive and curr_inactive == orig_inactive
    
                # shutdown/startup to clear reset
                # shutdown/startup all subports related to intf
                for sub in subports:
                    cli_interface_shutdown(sub)
                time.sleep(_DELAY_AFTER_IF_SHUTDOWN_S)
                for sub in subports:
                    cli_interface_startup(sub)

            print('test_download_post_run_reset ', intf, ' done') # TEMPORARY DEBUG


def test_the_fw_tests():
    '''
    @summary: TEMPORARY test code: Run all tests in this file.
    '''
    print('test_the_fw_tests BEGIN')
    util_wrapper_init()

    test_download_invalid_fw(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_valid_fw(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_kill(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_abort(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)
    
    test_download_lpmode(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_reset(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_run(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_commit(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    test_download_post_run_reset(my_duthosts, my_enum_rand_one_per_hwsku_frontend_hostname,
                        my_enum_frontend_asic_index, my_conn_graph_facts, my_xcvr_skip_list)

    print('test_the_fw_tests END')

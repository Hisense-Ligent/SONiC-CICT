''' Wrappers for running transceiver onboarding tests locally on switch/router 
instead of remotely on server.

Might be usable for remote as well as local use, but there may be more appropriate 
(database based) APIs for remote use.
'''
import os
import re
import sys
import signal,subprocess
from time import sleep
from contextlib import contextmanager


_CLI_WRAP_DBG = True


#----------------------------------------------------------------------------
# CLI command wrappers
#----------------------------------------------------------------------------

def cli_wrap(cmdstr, paramstr=None):
    ''' Wrapper to execute CLI commands
    cmdstr      command string to be split
    paramstr    optional additional string NOT to be split (e.g. for date +format)
    '''
    cmd_items = cmdstr.split()
    if paramstr:
        cmd_items.append(paramstr)
    resp = subprocess.run(cmd_items, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if resp.returncode != 0:
        if _CLI_WRAP_DBG:
            print('cli_wrap ERR   :', resp.returncode, ":", resp.stderr.decode("utf-8"))
            print('cli_wrap args  :', resp.args)
            print('cli_wrap stdout:', resp.stdout.decode("utf-8"))
        return None
    return resp.stdout.decode("utf-8")

def cli_wrap_sh(cmdstr):
    ''' Wrapper to execute CLI commands as ibe single command
    cmdstr      command string to be split
    '''
    cmd_items = [cmdstr]
    resp = subprocess.run(cmd_items, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    if resp.returncode != 0:
        if _CLI_WRAP_DBG:
            print('cli_wrap_sh ERR   :', resp.returncode, ":", resp.stderr.decode("utf-8"))
            print('cli_wrap_sh args  :', resp.args)
            print('cli_wrap_sh stdout:', resp.stdout.decode("utf-8"))
        return None
    return resp.stdout.decode("utf-8")

def cli_wrap_sh_grep(cmdstr):
    '''Special case of cli_wrap_sh for commands including 'grep'.
    
    'grep' returns return code 0 if there are matches, return code 1 if no matches.
    So here we allow return code 1 and check stderr as well.
    '''
    cmd_items = [cmdstr]
    resp = subprocess.run(cmd_items, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    if (resp.returncode != 0 and resp.returncode != 1) or (len(resp.stderr) > 0):
        if _CLI_WRAP_DBG:
            print('cli_wrap_sh ERR:', resp.returncode, ":", resp.stderr.decode("utf-8"))
            print('cli_wrap_sh args:', resp.args)
        return None
    return resp.stdout.decode("utf-8")


def cli_output2dict(clistr, delimiter=':'):
    '''Try to make a dict from CLI output. 
    E.g., CLI line "serial number : 12345678" would add dict entry 'serial number' : '12345678'
    This is hard to make truly general; may fail for some CLI commands.
    '''
    outdict = dict()
    clilines = clistr.splitlines()
    for line in clilines:
        try:
            k,v = line.strip().split(delimiter)
            k = k.strip()
            v = v.strip()
            outdict[k] = v
        except:
            # ignore lines that don't fit
            continue
    return outdict


#----------------------------------------------------------------------------
# interface related CLi commands
#----------------------------------------------------------------------------

def _valid_portname(portname):
    # Only checking naming format, NOT if port exists. (For example, Ethernet45
    # may be valid and yet not exist.)
    l = len(portname)
    if l > len('Ethernet') and portname[:8] == 'Ethernet' and portname[8:].isnumeric():
        return True
    return False

def cli_interface_desc(portname):
    ''' Return tuple (name, oper state, admin state, alias)

    Cisco:
    show interfaces description Ethernet96
      Interface    Oper    Admin    Alias    Description
    -----------  ------  -------  -------  -------------
     Ethernet96      up       up    etp12

    Arista:
    show int desc Ethernet96
    Interface    Oper    Admin         Alias    Description
    -----------  ------  -------  ------------  -------------
    Ethernet96    down     down  Ethernet25/1
    '''
    name = None; oper = None; admin = None; alias = None
    cmdstr = "show interfaces description " + portname
    resp = cli_wrap(cmdstr)
    if resp:
        lines = resp.splitlines()
        if len(lines) > 2:
            words = lines[2].split()
            if len(words) >= 3:
                name = words[0]
                oper = words[1]
                admin=words[2]
            alias = None
            if len(words) >= 4:
                alias=words[3]
    return (name,oper,admin,alias)


def cli_interface_present(portname):
    ''' Return True if transceiver is present, False otherwise (incl. on error).
    '''
    pres = False

    cmdstr  = 'sudo sfputil show presence -p ' + portname
    clistr  = cli_wrap(cmdstr)
    lines   = clistr.splitlines()
    line    = lines[2]
    items   = line.split()
    if items[0] == portname and items[1] == 'Present':
        pres = True

    return pres


def cli_interface_admin_status_up(portname):
    ''' Return True if interface admin stat  is up, False otherwise (incl. invalid name).
    '''
    up = False
    if _valid_portname(portname):
        resp = cli_interface_desc(portname)
        if resp:
            up = resp[2]
            if up:
                up = up.lower() == 'up'
    return up

def cli_interface_oper_status_up(portname):
    ''' Return True if interface oper state is up, False otherwise (incl. invalid name).
    '''
    up = False
    if _valid_portname(portname):
        resp = cli_interface_desc(portname)
        if resp:
            up = resp[1]
            if up:
                up = up.lower() == 'up'
    return up


def cli_interface_num_hostlanes(portname):
    '''Return number of host lanes for port.

    Using "interface" level info instead of transceiver-specifics to try to 
    make this as general as possible.
    '''
    num_lanes = None
    cmdstr  = 'show interfaces status ' + portname
    clistr  = cli_wrap(cmdstr)                          # run CLI command
    lines = clistr.splitlines()
    line  = lines[2]
    items = line.split()
    lanes = items[1]
    num_lanes = len(lanes.split(','))
    return num_lanes

def cli_interface_hostlanes(portname, namespace = ''):
    '''Return first and last host lanes for port. 0-based numbering.
    For breakout ports this depends on subport.
    '''
    num_lanes = cli_interface_num_hostlanes(portname)
    subport   = cli_interface_subport(portname, namespace)
    if not subport:
        startlane = 0
        endlane   = num_lanes-1
    else:
        startlane = (subport-1)*num_lanes
        endlane   = startlane + num_lanes -1
    return startlane,endlane


def cli_interface_num_medialanes(portname):
    '''Return number of media lanes for port.
    '''
    num_lanes = None
    cmdstr = 'show interfaces transceiver eeprom ' + portname
    clistr  = cli_wrap(cmdstr)                          # run CLI command
    clidict = cli_output2dict(clistr, delimiter=':')    # decode output
    if 'Media Lane Count' in clidict:
        num_lanes = clidict['Media Lane Count']
        try:
            num_lanes = int(num_lanes)
        except:
            num_lanes = None
    return num_lanes

def cli_interface_medialanes(portname, namespace = ''):
    '''Return first and last media lanes for port. 0-based numbering.
    For breakout ports this depends on subport.
    '''
    num_lanes = cli_interface_num_medialanes(portname)
    subport   = cli_interface_subport(portname, namespace)
    if not subport:
        startlane = 0
        endlane   = num_lanes-1
    else:
        startlane = (subport-1)*num_lanes
        endlane   = startlane + num_lanes -1    
    return startlane,endlane


def cli_interface_number(portname):
    ''' Get API number for named interface.
    Cisco  alias examples: etp12 or etp5a
    Arista alias examples: Ethernet25/1 or Ethernet25/5
    
    For split ports, ignore the last a/b or 1/5; API number is the same 
    for both; there's no API support for sub-ports.
    '''
    num = None  # (or -1 ?)
    if not _valid_portname(portname):
        return num
    resp = cli_interface_desc(portname)
    alias = resp[3]
    if not alias:
        return num

    # This works on Cisco and Arista, but since format isn't standardized
    # there may be other platforms where this doesn't work.
    s = ''
    i = 0; l = len(alias)
    while i < l and not alias[i].isnumeric():
        i += 1
    while i < l and alias[i].isnumeric():
        s += alias[i]
        i += 1
    # ignore any trailing non-numerical subport stuff like a, b, /5, ...
    if len(s):
        num = int(s)

    return num


def cli_interface_physport(port, namespace):
    '''Return physical port of <port>.
    '''
    physport = None

    #sonic-db-cli -n "" CONFIG_DB hget "PORT|Ethernet4" "index"
    #0
    cmd = 'sonic-db-cli -n "' + namespace + '" CONFIG_DB hget "PORT|' + port + '" "index"'
    clistr = cli_wrap_sh(cmd)
    assert clistr , '%s failed' % (cmd)
    line = clistr.splitlines()[0]
    assert line and len(line)
    s = line.split()[0]
    physport = int(s)

    return physport


def cli_interface_subport(port, namespace):
    '''Return subport number, or 0 for non-breakout ports.
    '''
    subport = 0
    cmd = 'sonic-db-cli -n "' + namespace + '" CONFIG_DB hget "PORT|' + port + '" "subport"'
    clistr = cli_wrap_sh(cmd)
    assert clistr , '%s failed' % (cmd)
    line = clistr.splitlines()[0]
    if len(line):
        s = line.split()[0]
        subport = int(s)
    return subport


def cli_interface_first_subport(port, portlist, namespace):
    '''Return first subport of same physical port of <port> in <portlist>.

    E.g., if physical port 1 has subports Ethernet0 and Ethernet4, return
    Ethernet0 for both <port>=Ethernet0 and <port>=Ethernet4 calls.
    
    Return <port> itself if <port> is not part of a breakout group.
    Return <None> if a first subport was not found or on error.

    Requires that <portlist> is sorted properly corresponding to switch's port
    sequence (e.g. Ethernet2 before Ethernet10), not just lexicographically.
    '''
    if not port or not portlist:
        return None

    firstsub = None

    cmd = 'sonic-db-cli -n "' + namespace + '" CONFIG_DB hget "PORT|' + port + '" "subport"'
    clistr = cli_wrap_sh(cmd)
    assert clistr , '%s failed' % (cmd)
    line = clistr.splitlines()[0]

    if not line or line == '0':
        # no breakout, <port> is the only port
        return port
    elif line == '1':
        # breakout, <port> is the first subport
        return port
    else:
        # Search backwards through list starting from <port> for first subport 1.
        # This requires that <port> isn't first in the list.
        port_idx = portlist.index(port)
        if port_idx < 1:
            return None

        for idx in reversed(range(port_idx)):
            check_port = portlist[idx]
            cmd = 'sonic-db-cli -n "' + namespace + '" CONFIG_DB hget "PORT|' + check_port + '" "subport"'
            clistr = cli_wrap_sh(cmd)
            assert clistr , '%s failed' % (cmd)
            line = clistr.splitlines()[0]
            if line and line == '1':
                # Check if <check_port> and <port> are subports of same physical port.
                # Otherwise keep searching.
                if cli_interface_physport(check_port,namespace) == cli_interface_physport(port,namespace):
                    firstsub = check_port
                    break

    return firstsub


def cli_physport_all_subports(physport, portlist, namespace=''):
    '''Return list of ALL subports of <physport> in <portlist>.
    '''
    subports = []

    for port in portlist:
        pp = cli_interface_physport(port, namespace)
        if pp != None and pp == physport:
            subports.append(port)

    return subports

def cli_interface_all_subports(port, portlist, namespace=''):
    '''Return list of ALL subports of the same physical port that <port> 
    is a (sub)port of.
    '''
    subports = []

    physport = cli_interface_physport(port, namespace)
    if physport != None:
        subports = cli_physport_all_subports(physport, portlist, namespace)

    return subports


def cli_interface_sort(portlist, namespace=''):
    '''Sort interface list "interfaceographically".

    The usual lexicographical sort won't do; "Ethernet2" should come before 
    "Ethernet10", and there may be different naming styles like "Ethernet1/4"
    or "etp5a" or..

    TBD: The current implementation may not handle interface naming styles
        other than "Ethernet4" correctly.
    '''
    port_dict = {}

    for p in portlist:
        phys = cli_interface_physport(p, namespace)
        sub  = cli_interface_subport(p, namespace)
        port_dict[(phys,sub)] = p

    sorted_list = []
    for x in sorted(port_dict):
        sorted_list.append(port_dict[x])

    return sorted_list


def cli_interface_shutdown(portname):
    '''sudo config interface shutdown
    '''
    rc = 1 # ERR
    if not _valid_portname(portname):
        return rc
    cmdstr = "sudo config interface shutdown " + portname
    resp = cli_wrap(cmdstr)
    if resp:
        rc = 0 # OK
    return rc

def cli_interface_startup(portname):
    '''sudo config interface startup
    '''
    rc = 1 # ERR
    if not _valid_portname(portname):
        return rc
    cmdstr = "sudo config interface startup " + portname
    resp = cli_wrap(cmdstr)
    if resp:
        rc = 0 # OK
    return rc


#----------------------------------------------------------------------------
# various CLI utility functions
#----------------------------------------------------------------------------

def cli_time():
    ''' Get timestamp in same format as syslog.
    date +%b %d %H:%M:%S.%6N

    sample output:  Jul 12 20:11:38.355257
    syslog format:  Jul 12 19:46:29.053313
    '''
    resp = None
    fmt = '+%b %d %H:%M:%S.%6N'
    resp = cli_wrap('date', fmt)
    if resp:
        resp = resp.strip() # remove "\n"
    return resp


def cli_syslog_grep_last_n(target, n):
    '''Get (up to) last 'n' lines that include the text <target> in syslog.

    target  search text to grep for
    n       max number of log lines to return
    '''
    # cli_wrap doesn't work for pipes ("|"), need a 2-step implementation
    cmdstr1 = 'sudo cat /var/log/syslog '
    #cmdstr1 = 'sudo tail -n400 /var/log/syslog '
    #cmdstr1 = 'show logging '    # doesn't work here
    cmdstr2 = 'grep ' + target
    cmd1_items = cmdstr1.split()
    cmd2_items = cmdstr2.split()
    p1 = subprocess.Popen(cmd1_items, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p2 = subprocess.Popen(cmd2_items, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    p1.stdout.close()       # allow p1 to receive a SIGPIPE if/when p2 exits
    resp = p2.communicate() # returns a tuple (stdout_data, stderr_data)
    if resp[1]:
        if _CLI_WRAP_DBG: print('ERR:', resp[1].decode("utf-8"))
        return None

    # return last n lines
    lines = resp[0].decode("utf-8").splitlines()
    return lines[-n:]


def cli_parse_float_with_unit(s):
    ''' Parse text containing a float value plus unit "compress" against the 
    numeric value. E.g. "3.3V".
    '''
    f = None
    try:
        f = re.sub("[^0-9.\-]","", s)
        f = float(f)
    except:
        pass
    return f


def cli_fw_version(portname):
    ''' Return tuple of strings (active FW ver, INactive FW ver)

    admin@sonic:~$ sudo sfputil show fwversion Ethernet0
    Image A Version: 0.5.0
    Image B Version: N/A
    Factory Image Version: 0.0.0
    Running Image: A
    Committed Image: A
    Active Firmware: 0.5.0
    Inactive Firmware: 9.3.0
    '''
    active = None; inactive = None
    cmdstr = "sudo sfputil show fwversion  " + portname
    resp = cli_wrap(cmdstr)
    if resp and not 'not implemented' in resp:
        lines = resp.splitlines()
        if len(lines) >= 7:
            active   = lines[5].split()[-1]
            inactive = lines[6].split()[-1]

    return (active, inactive)

def cli_committed_fw_bank_ver(portname):
    ''' Return tuple of strings (committed FW bank, committed FW ver)
    '''
    bank = None; ver = None
    cmdstr = "sudo sfputil show fwversion  " + portname
    resp = cli_wrap(cmdstr)
    if resp and not 'not implemented' in resp:
        lines = resp.splitlines()
        if len(lines) >= 7:
            a_ver = lines[1].split()[-1]
            b_ver = lines[2].split()[-1]
            bank  = lines[4].split()[-1]
            ver = b_ver if bank == 'B' else a_ver

    return (bank, ver)


def cli_link_last_up_downtime(intf, namespace):
    '''Return tuple of last time link went up and down, respectively.
    
    Returns times as strings, e.g. 'Thu Oct 03 16:18:15 2024', incl. empty 
    strings ('') if link never went up or down. Returns None in case of error.

    #admin@sonic:~$ sonic-db-cli -n '' APPL_DB hget "PORT_TABLE:Ethernet96" "last_up_time"
    #Thu Oct 03 16:18:15 2024
    #admin@sonic:~$ sonic-db-cli -n '' APPL_DB hget "PORT_TABLE:Ethernet96" "last_down_time"
    # 
    #admin@sonic:~$
    '''
    up = None; dn = None

    # sonic-db-cli -n '<namespace>' APPL_DB hget "PORT_TABLE:<port>" "last_up_time"
    # sonic-db-cli -n '<namespace>' APPL_DB hget "PORT_TABLE:<port>" "last_down_time"
    cmdstr_up = 'sonic-db-cli -n "' + namespace + '" APPL_DB hget "PORT_TABLE:' + intf + '" "last_up_time"'
    cmdstr_dn = 'sonic-db-cli -n "' + namespace + '" APPL_DB hget "PORT_TABLE:' + intf + '" "last_down_time"'

    resp = cli_wrap_sh(cmdstr_up)
    if resp:
        lines = resp.splitlines()
        if len(lines) >= 1:
            try:
                up = lines[0]
            except:
                up = None

    resp = cli_wrap_sh(cmdstr_dn)
    if resp:
        lines = resp.splitlines()
        if len(lines) >= 1:
            try:
                dn = lines[0]
            except:
                dn = None

    return (up, dn)

def cli_link_flap_count(intf, namespace):
    '''Return link flap count.

    Returns flap count as integer value, None in case of error.

    Note: After reboot and first link up, there's an initial flap count of 1.

    #admin@sonic:~$ sonic-db-cli -n '' APPL_DB hget "PORT_TABLE:Ethernet96" "flap_count"
    #1
    '''
    flaps = None

    # sonic-db-cli -n '<namespace>' APPL_DB hget "PORT_TABLE:<port>" "flap_count"
    cmdstr = 'sonic-db-cli -n "' + namespace + '" APPL_DB hget "PORT_TABLE:' + intf + '" "flap_count"'

    resp = cli_wrap_sh(cmdstr)
    if resp:
        lines = resp.splitlines()
        if len(lines) >= 1:
            try:
                flaps = lines[0].split()[-1]
                flaps = int(flaps)
            except:
                flaps = None

    return flaps


def cli_chassis_mac(namespace):
    '''Return Chassis MAC as a string (e.g. '40:14:82:8A:16:00'), None on error.
    '''
    macstr = None

    cmdstr = 'show platform syseeprom'
    resp = cli_wrap_sh(cmdstr)
    if resp:
        lines = resp.splitlines()
        if len(lines) >= 16:
            for line in lines:
                if 'Base MAC' in line:
                    macstr = line.split()[-1]
                    break

    return macstr


#----------------------------------------------------------------------------
# COntext Managers
#----------------------------------------------------------------------------

@contextmanager
def cli_dom_disabled(intf, namespace):
    '''
    09/10/24 Mihir wants generic contextmgr based function for disabling DOM polling:
        Using a context manager will allow us to create a generic method which can
        be reused in multiple test functions. 
        In other terms, a test case requiring disabling DOM can do something like below
        def test_x_feature():
            with cli_dom_disabled():
                #Perform the tests

        @contextmanger
        def cli_dom_disabled():
            try:
                #Disable DOM
                yield
            finally:
                #Enable DOM

    For breakout ports:
        You will always need to disable DOM on subport 1 for breakout ports since 
        subport 1 will indeed control the DOM monitoring for all the remaining 
        subports in the port breakout group. This also means that if the test is 
        running on subport 2, the CLI should be executed on subport 1 (CLI will 
        fail if user tries to disable DOM on subport != 1 for breakout ports).
    '''

    # Only relevant for non-breakout or breakout subport 1. We COULD find the
    # first subport here and disable on that, but the caller needs to know the
    # type of port it's dealing with; better to assert here and fix the caller.
    sub_port = cli_interface_subport(intf, namespace)
    # 0 = no breakout, 1 = first subport
    assert sub_port == 0 or sub_port == 1, 'SW error: DOM disable N/A on subport %d' % (sub_port)

    cmd_dom_poll_dis = 'sudo config interface -n "' + namespace + '" transceiver dom ' + intf + ' disable'
    cmd_dom_poll_ena = 'sudo config interface -n "' + namespace + '" transceiver dom ' + intf + ' enable'
    cmd_dom_poll_chk = 'sonic-db-cli -n "' + namespace + '" CONFIG_DB hget "PORT|' + intf + '" "dom_polling"'

    try:
        # DISable DOM polling
        clistr = cli_wrap_sh(cmd_dom_poll_dis)
        assert clistr != None, '%s failed' % (cmd_dom_poll_dis)

        # verify DOM polling off
        clistr = cli_wrap_sh(cmd_dom_poll_chk)
        assert clistr , '%s failed' % (cmd_dom_poll_chk)
        line = clistr.splitlines()[0]
        assert 'disabled' in line.lower(), 'failed to disable DOM poll'

        # leave to do whatever operations..
        yield
    
    finally:
        # ENable DOM polling
        clistr = cli_wrap_sh(cmd_dom_poll_ena)
        assert clistr != None, '%s failed' % (cmd_dom_poll_ena)

        # verify DOM monitoring back on
        clistr = cli_wrap_sh(cmd_dom_poll_chk)
        assert clistr , '%s failed' % (cmd_dom_poll_chk)
        line = clistr.splitlines()[0]
        assert 'enabled' in line.lower(), 'failed to re-enable DOM poll'



#----------------------------------------------------------------------------
# Framework for starting, polling, and killing processes.
# Intended for longer-running tasks like FW download.
# (Not directly CLI related, but had to put them somewhere.)
# Because we create the  process with “shell=True”, the download ends up in a 
# child process of the child process, so it will keep running after the PID 
# created is killed. Use a process group and killing that seems to work.
#----------------------------------------------------------------------------

def cli_proc_spawn(cmdstr):
    '''Start pollable, killable process.

    Returns Popen object.
    
    Note: Make sure to always call cli_proc_kill afterwards! The process is 
          NOT automatically terminated when <cmdstr> completes.
    '''
    # exec (requires plain cmdstr as arg) + 'shell=True' makes process killable
    # - but may wipe out stdout/stderr?
    cmdstr = 'exec ' + cmdstr

    #p = subprocess.Popen(cmdstr, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    p = subprocess.Popen(cmdstr, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       shell=True, preexec_fn=os.setsid)
    return p

def cli_proc_running(p):
    '''Return True if process still running, False otherwise.
    '''
    rc = p.poll()
    return True if (rc == None) else False

def cli_proc_kill(p):
    '''Kill process.
    '''
    #p.kill()
    os.killpg(os.getpgid(p.pid), signal.SIGTERM)


def cli_proc_read_output(p):
    '''Poll/read latest output as list of lines, empty list by default.

    Reads from stdout stream so any output is only returned once.
    So, we should be able to use this for polling e.g. download output while 
    it's ongoing(?)
    '''
    return p.stdout.readlines()

def cli_proc_read_errors(p):
    '''Poll/read latest error messages as list of lines, empty list by default.
    '''
    return p.stderr.readlines()


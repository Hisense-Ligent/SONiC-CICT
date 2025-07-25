'''Test config wrappers/utilities

Ignoring distinction between switch/router here, referring to both as "switch".

Might be usable for remote as well as local use, but there may be other, more
more efficient APIs for remote use.
'''
import os
import sys
import yaml # PyYAML ver 6.0.1(PC), 5.4.1(SONiC.20230531.30)

from api_wrapper import *   # get_StartCmdPayloadSize
from cli_wrapper import *   # cli_interface_sort


# Local Constants
TEST_CFG_DEFAULT_FILENAME = 'transceiver_static_info.yaml'


def test_cfg_read(fname = TEST_CFG_DEFAULT_FILENAME):
    '''Read and parse transceiver_static_info.yaml test config file.

    Return results as dict, or None on error.
    
    TBD: YAML line "cmis_rev: 5.2" will be parsed as 'cmis_rev': 5.2. I.e.,
    you get a numeric value. And since YAML is hierarchical, just turning 
    everything into strings would make the whole yyy['topology'] one big
    string for example.
    For now, ignoring this and assuming the YAML file will be all strings.
    So, you need to quote numbers in the YAML file as in "cmis_rev: '5.2'"
    '''
    yyy = None

    try:
        with open(fname) as instream:
            yyy = yaml.safe_load(instream)
    except yaml.YAMLError as ex:
        print(ex)
    except:
        print('test_cfg_get file read error')

    return yyy


def test_cfg_valid(yyy):
    '''Partial validation of YAML config.

    topology:
        <device_name>:
            <port_name>:
                active_firmware: <active_firmware_version>
                ...

    There should be at least one switch/router and one port.
    '''
    valid = False
    try:
        k1 = list( yyy.keys() )
        if k1[0] == 'topology':
            k2 = list( yyy[k1[0]].keys() )
            if len(k2) and len(k2[0]) > 3:      # min switch name length?
                k3 = list( yyy[k1[0]][k2[0]].keys() )
                if len(k3) and len(k3[0]) > 8 and k3[0][:8]=='Ethernet':
                    valid = True
    except Exception as ex:
        print('test_cfg_valid exception', ex)   # skip this print; kind'a redundant?
    return valid


def test_cfg_switches(yyy):
    '''Get list of swith names
    '''
    switches = None
    try:
        switches = []
        switches = list( yyy['topology'].keys() )
    except:
        print('test_cfg_switches error')
    return switches

def test_cfg_switchcfg(yyy, switchname=None):
    '''Get cfg for specified switch (default is first switch)
    '''
    cfg = None
    try:
        sws = test_cfg_switches(yyy)
        if not switchname:
            switchname = sws[0]
        if switchname and len(switchname) and switchname in sws:
            cfg = yyy['topology'][switchname]
    except:
        print('test_cfg_switchcfg error')
    return cfg


def test_cfg_ports(yyy, switchname=None, namespace=''):
    '''Get list of port names for specified switch (default is first switch).
    '''
    swcfg = test_cfg_switchcfg(yyy, switchname)
    ports = None
    try:
        ports = list( swcfg.keys() )
    except:
        print('test_cfg_ports error')

    # Sort the list "interfaceographically".
    ports = cli_interface_sort(ports, namespace)

    return ports

def test_cfg_portcfg(yyy, switchname=None, portname=None, namespace=''):
    '''Get cfg for specified port (default is first port)
    '''
    swcfg = test_cfg_switchcfg(yyy, switchname=None)
    portcfg = None
    if not portname:
        portname = test_cfg_ports(yyy, switchname, namespace)[0]
    try:
        portcfg = swcfg[portname]
    except:
        print('test_cfg_portcfg error')
    return portcfg


def test_cfg_fw_img_ver(yyy, switchname, portname, namespace=''):
    '''Return version of download image.
    '''
    assert switchname
    assert portname

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'
    assert test_cfg_valid(test_cfg)
    port_cfg = test_cfg_portcfg(test_cfg, switchname, portname, namespace)
    assert port_cfg

    img_ver = None

    try:
        img_ver = port_cfg['firmware_valid_image_ver']
    except:
        # Don't assert; not all txceivers may have download file and/or version.
        # And don't even print anything; we just won't verify the version.
        pass

    return img_ver

def test_cfg_fw_img_path(yyy, switchname, portname, invalid=False, namespace=''):
    '''Return VALID or INVALID download image file path
    
    TBD: Where to get filename? For now, assume it's added to test cfg YAML file.

    There's no invalid image provided, we invalidate a copy on the fly. We COULD
    use ‘dd’, but that seems risky (path specified in cfg file could be anything).
    # copy
    dd if=validfile.bin of=INvalidfile.bin
    # muck up 1 byte at offset 200
    dd if=/dev/random of=INvalidfile.bin iflag=count_bytes oflag=seek_bytes seek=200 count=1 conv=notrunc
    '''
    assert switchname
    assert portname

    img_path = None
    inval_path = None

    test_cfg = test_cfg_read()
    assert test_cfg, 'Failed to read test config file'
    assert test_cfg_valid(test_cfg)
    port_cfg = test_cfg_portcfg(test_cfg, switchname, portname, namespace)
    assert port_cfg

    try:
        img_path = port_cfg['firmware_valid_image']
        if not os.path.isfile(img_path):
            print('test_cfg_fw_img_path(%s, %s): no %s image' % (switchname, portname, img_path))
            img_path = None
    except:
        # don't assert here; not all txceivers may have download files
        print('test_cfg_fw_img_path(%s, %s): no image path' % (switchname, portname))
        img_path = None

    if img_path and invalid:
        # create corrupt file unless aleady done
        inval_path = 'inval_' + img_path
        #if not os.path.isfile(inval_path):
        if os.path.isfile(inval_path):
            img_path = inval_path
        else:
            try:
                # copy
                cmdstr = 'cp' + ' ' + img_path + ' ' + inval_path
                resp = cli_wrap_sh(cmdstr)
                #if not os.path.isfile(inval_path):
                #    print('test_cfg_fw_img_path(%s, %s): failed to copy %s' % (switchname, portname, img_path)
                assert os.path.isfile(inval_path), 'failed to copy %s' % (img_path)

                # muck up x bytes at offset y
                byte_count = 1
                #byte_offset = 200
                # Byte offset must be greater than StartCmdPayloadSize in order
                # for the error to be in the downloaded part of the image.
                headersize = get_StartCmdPayloadSize(portname)
                assert headersize != None
                byte_offset = headersize + 200
                #byte_offset = 4096+200  # TEMPORARY: try byte 4096+200 = 4296 instead
                print('test_cfg_fw_img_path(): corrupting byte ', byte_offset) # TEMPORARY DEBUG

                with open(inval_path, 'r+b') as f:
                    f.seek(byte_offset)
                    vals = f.read(byte_count)
                    vals = bytearray(vals)
                    for i in range(byte_count):
                        vals[i] = vals[i] ^ 0xff

                    f.seek(byte_offset)
                    f.write(vals)
                    f.close()

                img_path = inval_path
            except:
                print('test_cfg_fw_img_path(%s, %s): failed to create invalid file' % (switchname, portname))
                img_path = None

            assert os.path.isfile(inval_path), 'failed to create %s' % (inval_path)

    return img_path

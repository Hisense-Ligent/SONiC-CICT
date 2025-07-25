'''api_wrapper.py

Wrappers for transceiver-related APIs (optoe, sfp, sfp_base, xcvr_api, cmis, ...).
Not replacing anything; trying to share common patterns of API use.

Should be usable for remote as well as local use, but there may be other, more
efficient APIs for remote use.
'''
from sonic_platform.platform import Platform
from cli_wrapper    import *   # wrappers for CLI etc.


_API_WRAP_DBG = True


# Module types. Based on SFF-8024 R4.10 and xcvr_api_factory.py.
CMIS_IDs    = [0x18, 0x19, 0x1e, 0x1f, 0x20]    # QSFP-DD, OSFP, QSFP+/112, SFP-DD, SFP+
SFF8436_IDs = [0x0d]                            # QSFP+(if rev <  3)
SFF8636_IDs = [0x0d, 0x11]                      # QSFP+(if rev >= 3), QSFP28
SFF8472_IDs = [0x03]                            # SFP, SFP+, SFP28


def _get_sfp(intf):
    ''' Get SFP API
    '''
    sfp = None
    try:
        port_num = cli_interface_number(intf)
        if port_num != None:
            sfp = Platform().get_chassis().get_sfp(port_num)
    except:
        if _API_WRAP_DBG:
            print('_get_sfp() ERR, exception')
    return sfp

def _get_api(intf):
    ''' Get xcvr API (CMIS or SFF-whatever API)
    '''
    api = None
    try:
        port_num = cli_interface_number(intf)
        if port_num != None:
            sfp = _get_sfp(intf)
            api = sfp.get_xcvr_api()
    except:
        if _API_WRAP_DBG:
            print('_get_api() ERR, exception')
    return api


def _get_id(intf):
    '''Get ID which indicates transceiver/protocol type. Always byte 0.
    '''
    # no existing API to get this as numeric, have to re-read from module?
    id = 0
    api = _get_api(intf)
    if api:
        try:
            sfp = _get_sfp(intf)
            id  = sfp.read_eeprom(0,1)[0]
        except:
            if _API_WRAP_DBG:
                print('_get_id:ERR, exception')
    return id

def _get_rev_compliance(intf):
    '''Get revision compliance ~ protocol rev. Usually byte 1 (when supported).
    '''
    # no existing API to get this as numeric, have to re-read from module?
    rev = 0
    api = _get_api(intf)
    if api:
        try:
            sfp = _get_sfp(intf)
            rev = sfp.read_eeprom(1,1)[0]
        except:
            if _API_WRAP_DBG:
                print('_get_rev_compliance: ERR, exception')  # ?
    return rev

def _get_spec_compliance(intf):
    '''Get specification compliance
    
    cmis    page 00h byte 85        {1,2=optical, 3=passive Cu, 3=active cable, 5=Base-T}
    sff8436 page 00h byte 131-138   {}
    sff8472 A0h byte 8 bit 3:2      {00=optical, 01=passive cable, 10=active cable}
    sff8636 page 00h byte 131-138   {}
            page 00h byte 192       {extended SFF8024}
    '''
    spec_compl = 0
    api = _get_api(intf)
    if api:
        try:
            sfp = _get_sfp(intf)
            if is_cmis(intf):
                spec_compl = sfp.read_eeprom(85,1)[0]  # media type
            elif is_sff8436(intf) or is_sff8636(intf):
                spec_compl = sfp.read_eeprom(131,1)[0] # TBD: need 131-138, 192(extended) ?
            elif is_sff8472(intf):
                spec_compl = sfp.read_eeprom(8,1)[0]   # bit 3:2
            else:
                return None # or return spec_compl = 0?
        except:
            if _API_WRAP_DBG:
                print('_get_spec_compliance: ERR, exception')  # ?
    return spec_compl


def is_cmis(intf):
    '''Return True if transceiver is CMIS-based, False otherwise (incl. on error).
    '''
    rc = False
    id = _get_id(intf)
    if id and id in CMIS_IDs:
        rc = True
    return rc

def is_sff8436(intf):
    '''Return True if transceiver is SFF8436-based, False otherwise (incl. on error).
    '''
    rc = False
    id = _get_id(intf)
    #if id and id in SFF8436_IDs:
    if id:
        if id == 0x0d and _get_rev_compliance(intf) < 3:
            rc = True
    return rc

def is_sff8636(intf):
    '''Return True if transceiver is SFF8636-based, False otherwise (incl. on error).
    '''
    rc = False
    id = _get_id(intf)
    #if id and id in SFF8636_IDs:
    if id:
        if id == 0x11:
            rc = True
        elif id == 0x0d and _get_rev_compliance(intf) >= 3:
            rc = True
    return rc

def is_sff8472(intf):
    '''Return True if transceiver is SFF8472-based, False otherwise (incl. on error).
    '''
    rc = False
    id = _get_id(intf)
    if id and id in SFF8472_IDs:
        rc = True
    return rc

def is_optical(intf):
    '''Return True if transceiver is optical, False otherwise (incl. on error).

    This does not cover all cases, but this file is expected to be replaced
    by existing APIs in the real (cloud based) implementation anyway.

    cmis    page 00h byte 85        {1,2=optical, 3=passive Cu, 3=active cable, 5=Base-T}
    sff8472 A0h byte 8 bit 3:2      {00=optical, 01=passive cable, 10=active cable}
    sff8436 or..
    sff8636 page 00h byte 131-138   {}
            page 00h byte 192       {extended SFF8024}
    '''
    id    = _get_id(intf)
    compl = _get_spec_compliance(intf)
    if id == None or compl == None:
        return False

    if id in CMIS_IDs:
        if compl in [1,2]:
            return True
        elif compl in [4]:  # "active cables" can be optical or copper
            # TBD: then how to distinguish electrical/optical
            #  - check page 0 byte 204-209 and assume all-zeroes ~ optical?
            return True

    elif id in SFF8472_IDs:
        if compl & 0x0C == 0:
            return True

    elif id in SFF8636_IDs: # treating 8436 and 8636 the same here
        if compl & 0x08:    # 40GBASE-CR4
            return False
        #if compl & 0x80:    # also check extended compliance
        else:
            return True

    return False

def has_lpmode(intf):
    '''Return True if transceiver supports LPMode, False otherwise (incl. on error).
    '''
    lp_support = is_optical(intf)   # TBD
    return lp_support

def is_coherent(intf):
    '''Return True if transceiver is CMIS/COherent, False otherwise (incl. on error).
    '''
    rc = False
    if is_cmis(intf):
        # The way to check seems to be via the media type of default app 1.
        #   3Eh (400ZR, DWDM amplified)
        #   3Fh (400ZR, single wavelength unamplified)
        # Ref.: OIF C-CMIS rev 1.2 sect.6.
        sfp = _get_sfp(intf)
        app1_media_type = sfp.read_eeprom(87,1)[0]
        if app1_media_type == 0x3E or app1_media_type == 0x3F:
            rc = True
    return rc


def get_StartCmdPayloadSize(intf):
    '''Get CDB CMD 0041h StartCmdPayloadSize.
    (Size of the image header that's not downloaded to the txceiver.)
    There doesn't seem to be any CLI support for getting this type of info(?)
    '''
    payloadsize = None
    try:
        api = _get_api(intf)

        # {'status': True,
        #  'info': txt, 
        #  'feature': (startLPLsize, maxblocksize, lplonly_flag, autopaging_flag, writelength)}
        fwfeats = api.get_module_fw_mgmt_feature(intf)['feature']
        payloadsize = fwfeats[0]
    except:
        pass
    return payloadsize


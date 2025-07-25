''' Wrapper replacement for tests/platform_tests/sfp/util.py 

The real util.py requires test environment with databases etc. which we don't
have when running locally on a switch/router. This wrapper replaces that with 
mostly dummy data.

Only intended for local use (i.e., in the switch/router), NOT intended for
normal, remote test setups.
'''
from test_cfg   import *   # wrappers dealing with test config file etc.


#----------------------------------------------------------------------------
# Default/dummy data.
# The following is just dummy data to use as placeholder parameters; just the
# minimum needed to run the tests locally in a switch/router.
#----------------------------------------------------------------------------

# duthost/duthosts[]
# ref.: ansible/devutil/devices/ansible_hosts.py AnsibleHostsBase + AnsibleHosts + AnsibleHost ?
# Big classes with lots of methods, iterators, __getitem__, etc.
class xxxhost():
    def __init__(self, hostname):
        self.hostname = hostname

    def get_namespace_from_asic_id(self, enum_frontend_asic_index):
        return ''

my_duthosts = []
# Note: The below values are just placeholders.
#       Build the real data based on test cfg file by calling util_wrapper_init().
arst_host = xxxhost('Arista-7050CX3-32S-C32')   # HwSKU
csco_host = xxxhost('Cisco-8101-O8C48')         # HwSKU
my_duthosts.append(arst_host)
my_duthosts.append(csco_host)

# indices
my_enum_rand_one_per_hwsku_frontend_hostname = 0
my_enum_frontend_asic_index = 0

# conn_graph_facts
# ref.: ansible/library/conn_graph_facts.py ?
my_conn_graph_facts = dict()

# xcvr_skip_list
# ref.: platform_tests/conftest.py xcvr_skip_list(duthosts)
my_xcvr_skip_list   = dict()
my_xcvr_skip_list[arst_host.hostname] = list()
my_xcvr_skip_list[csco_host.hostname] = list()


def util_wrapper_init():
    '''Initialize util_wrapper's dummy data with switch info from test config YML file.
    '''
    global my_duthosts, my_xcvr_skip_list
    my_duthosts.clear()
    my_xcvr_skip_list.clear()

    test_cfg = test_cfg_read()
    assert test_cfg_valid(test_cfg)

    for switchname in test_cfg_switches(test_cfg):
        sw_cfg = test_cfg_switchcfg(test_cfg, switchname)
        host = xxxhost(switchname)
        my_duthosts.append(host)
        my_xcvr_skip_list[switchname] = list()

    print('util_wrapper_init done')


#----------------------------------------------------------------------------
# Replacement functions
#----------------------------------------------------------------------------

def get_dev_conn(duthost, conn_graph_facts, asic_index, cfg_fname=None):
    ''' Wrapper replacement for tests/platform_tests/sfp/util.py

    Note: Added extra, optional cfg_fname parameter.
    Using default filename if cfg_fname is not specified.
    '''
    ##portmap seems to be some sort of per-ASIC dict of lists?
    #portmap = get_port_map(duthost, asic_index)
    portmap = dict()

    ##dev_conn seems to be a list of interface names?
    #dev_conn = ['Ethernet0', 'Ethernet4']
    dev_conn = list()

    if not cfg_fname:
        cfg_fname = TEST_CFG_DEFAULT_FILENAME # test_cfg.py
    
    yyy = test_cfg_read(cfg_fname)
    if yyy and test_cfg_valid(yyy):
        enum_frontend_asic_index = 0 # HACK, specific for local use
        namespace = duthost.get_namespace_from_asic_id(enum_frontend_asic_index)

        dev_conn = test_cfg_ports(yyy, switchname=None, namespace=namespace)
        #portmap.append( ??? )   # ?

    return portmap, dev_conn


# SONiC-CICT
Hisense opitical module SONiC test
## Testbed Topology（testcode_112224）

A total of 2 ports of a device with the onboarding transceiver should be connected with a cable. Each of these ports can be on the same device or different devices as well.

 Standalone topology with both ports connected on the same SONiC device (self loopback/SONIC.202405)

    ```text
    +-----------------+
    |          Port 13|<----+
    |                 |     | Loopback
    |    Device       |     | Connection
    |          Port 14|<----+
    |                 |
    +-----------------+
   ```
   ```text
  optical transceiver info：       
            400G AOC - updated for 2-bank
            active_firmware: '4.3.0'
            inactive_firmware: '4.2.0'
            cmis_rev: '5.0'
            vendor_date: '2024-03-04'
            vendor_name: Hisense
            vendor_oui: ac-4a-fe
            vendor_pn: DMQ8811A-EC08
            vendor_rev: '01'
            vendor_sn: UW4E3UM801C
            dual_bank_support: yes
            #firmware_valid_image_ver: '4.3.0'
            #firmware_valid_image: '400g_aoc_dmq8811a-ec+for_msft_sonic_v4p3.bi
    ```
    ```text
root@sonic:/home/admin/testcode_dmq8811_012225# show platform firmware status
Chassis     Module   Component   Version    Description
------------------------------------------------------
BIOS        N/A      0-241      BIOS - Basic Input Output System
Aikido      N/A      1.89       Aikido - x86 FPGA
TAM         N/A      2.6        TAM FW - x86
SSD         N/A      11.32      SSD
IOFPGA      N/A      1.10       Omega FPGA - Xilinx
eCPLD       N/A      1.9        Power CPLD
    ```



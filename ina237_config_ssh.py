#!/usr/bin/python3

import paramiko
import sys
import socket
import time
import re

host = sys.argv[1]
bus = sys.argv[2]
user = sys.argv[3]
password=sys.argv[4]

# Настройки для INA237 
i2caddr = [0x4a, 0x4b, 0x4e, 0x4f]
_get = i2cget
_set = i2cset

# Регистры
REG_CONFIG = 0x00
REG_ADC_CONFIG = 0x01
REG_SHUNT_CAL = 0x02
REG_VSHUNT = 0x04
REG_VBUS = 0x05
REG_DIETEMP = 0x06
REG_CURRENT = 0x07
REG_POWER = 0x08
REG_DIAG_ALRT = 0x0B
REG_SOVL = 0x0C
REG_SUVL = 0x0D
REG_BOVL = 0x0E
REG_BUVL = 0x0F
REG_TEMP_LIMIT = 0x10
REG_PWR_LIMIT = 0x11
REG_MANUFACTURER_ID = 0x3E
REG_DEVICE_ID = 0x3F

# Значения для калибровки и вычислений
max_voltage = 75
shunt_resistance = 0.005
max_current = 20
current_lsb = max_current/32768
power_lsb = 50 * current_lsb # 50 times current LSB
calibration_value = int((819.2 * 10**6) * (current_lsb * shunt_resistance))
calibration_value &= 0x7FFF # Ensure bit 15 is reserved (set to 0)

print ("Калибровочное значение:", hex(calibration_value))

with paramiko.SSHClient() as client:

    try:
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(host, username=user, password=password)
    except (socket.gaierror, socket.error, socket.timeout, TimeoutError, IOError, paramiko.ssh_exception.NoValidConnectionsError):
        print("Could not connect to %s \n" % host)
        sys.exit(1) 

    i = 0
    while i < 4:
        try:
            print (f"Фидер {i+1}:" )
            # Записываем калибровочное значение
            (stdin, stdout, stderr) = client.exec_command(f'i2cset -y 17 {i2caddr[i]} {REG_SHUNT_CAL} {calibration_value} w')
            time.sleep(0.1)
            (stdin, stdout, stderr) = client.exec_command(f'i2cget -y 17 {i2caddr[i]} {REG_SHUNT_CAL} w')
            try:
                for line in stdout:
                    print("Проверяем паравильность записи калибровки:", stdout.read().decode() )
            except line in stderr:
                print (line)
                sys.exit(1)

            (stdin, stdout, stderr) = client.exec_command(f'i2cget -y 17 {i2caddr[i]} {REG_VBUS} w')
            for line in stderr:
                print (line)
                sys.exit(1)
            raw_vbus = stdout.read().decode()
            vbus = int( ''.join(char for char in raw_vbus if char.isalnum()) , 16 )
            print ("Vin = {:.2f} V".format(vbus * 0.003125 ) )

            (stdin, stdout, stderr) = client.exec_command(f'i2cget -y 17 {i2caddr[i]} {REG_CURRENT} w')
            for line in stderr:
                print (line)
                sys.exit(1)
            raw_current = stdout.read().decode()
            current = int( ''.join(char for char in raw_current if char.isalnum()) , 16 )
            print ("Iin = {:.2f} A".format(current * current_lsb ) )

            (stdin, stdout, stderr) = client.exec_command(f'i2cget -y 17 {i2caddr[i]} {REG_POWER} w')
            for line in stderr:
                print (line)
                sys.exit(1)
            raw_power = stdout.read().decode()
            power = int( ''.join(char for char in raw_power if char.isalnum()) , 16 )
            power &= 0x00FFFFFF # Ensure bit 31-24 is reserved
            print ("Pin = {:.2f} W".format(power_lsb * power ) )

            (stdin, stdout, stderr) = client.exec_command(f'i2cget -y 17 {i2caddr[i]} {REG_DIETEMP} w')
            for line in stderr:
                print (line)
                sys.exit(1)
            raw_temperature = stdout.read().decode()
            temperature = int( ''.join(char for char in raw_temperature if char.isalnum()) , 16 )
            temperature &= 0xFFF0  # Ensure bits 0-3 are reserved (set to 0)
            print ("Temperature = {:.2f} °C".format((temperature >> 4) * 0.125 ) )

            print ('*****************')

            i = i + 1
        except (socket.gaierror, socket.error, socket.timeout, TimeoutError, IOError, paramiko.ssh_exception.NoValidConnectionsError) as error:
            print(error)
            sys.exit(1)

    client.close()

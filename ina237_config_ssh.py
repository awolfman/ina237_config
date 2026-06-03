#!/usr/bin/python3

import paramiko
import sys
import socket
import time

# Проверка аргументов командной строки
if len(sys.argv) < 5:
    print("Использование: script.py <host> <slot> <user> <password> <slot>")
    sys.exit(1)

host = sys.argv[1]
# bus = sys.argv[2]
slot = sys.argv[2]    # Номер I2C шины (передается в i2cset/i2cget)
user = sys.argv[3]
password = sys.argv[4]

# Функция для вычисления дополнения до двух (для знаковых чисел)
def twos_comp(val, bits):
# compute the 2's complement of int value val
    if (val & (1 << (bits - 1))) != 0: # if sign bit is set e.g., 8bit: 128-255
        val = val - (1 << bits)        # compute negative value
    return val

# Настройки для INA237 
i2caddr = [0x4a, 0x4b, 0x4e, 0x4f]

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
max_voltage = 75            # 75 В
shunt_resistance = 0.005    # 5 мОм
max_current = 20            # 20 А

current_lsb = max_current/32768
vbus_lsb = 3.125 * 10**(-3)         # LSB напряжения шины = 3.125 мВ
dietemp_lsb = 125 * 10**(-3)        # LSB температуры = 125 мК (0.125 °C)
conversion_factor = 5 * 10 **(-6)

power_lsb = 0.2 * current_lsb
calibration_value = int((819.2 * 10**6) * (current_lsb * shunt_resistance))
calibration_value &= 0x7FFF # Сброс 15-го бита по спецификации INA237

print("Рассчитанное калибровочное значение:", hex(calibration_value))

# Подключение по SSH
client = paramiko.SSHClient()

try:
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Подключение к {host}...")
    client.connect(host, username=user, password=password, timeout=10)
except (socket.gaierror, socket.error, paramiko.SSHException) as e:
    print(f"Не удалось подключиться к {host}: {e}")
    sys.exit(1)

try:
     for i in range(4):
         print(f"\n--- Фидер {i+1} (Адрес: {hex(i2caddr[i])}) ---")
         addr = i2caddr[i]
         
         # Запись калибровочного значения
         client.exec_command(f'i2cset -y {slot} {i2caddr[i]} {REG_SHUNT_CAL} {calibration_value} w')
         time.sleep(0.05)

         # Чтение и проверка калибровочного регистра
         _, stdout, _ = client.exec_command(f'i2cget -y {slot} {addr} {reg_shunt_cal} w')
         cal_read = parse_i2c_word(stdout.read().decode())
         print(f"Калибровка записана/прочитана: {hex(cal_read)}")
         
         # Чтение напряжения шины (VBUS)
         _, stdout, _ = client.exec_command(f'i2cget -y {slot} {addr} {reg_vbus} w')
         vbus_raw = parse_i2c_word(stdout.read().decode())
         vbus_actual = twos_comp(vbus_raw, 16) * vbus_lsb
         print(f"Напряжение шины: {vbus_actual:.3f} В")
            
         # Чтение тока (CURRENT)
         _, stdout, _ = client.exec_command(f'i2cget -y {slot} {addr} {reg_current} w')
         current_raw = parse_i2c_word(stdout.read().decode())
         current_actual = twos_comp(current_raw, 16) * current_lsb
         print(f"Ток: {current_actual:.3f} А")
        
         # Чтение мощности (POWER)
         _, stdout, _ = client.exec_command(f'i2cget -y {slot} {addr} {reg_power} w')
         power_raw = parse_i2c_word(stdout.read().decode())
         power_actual = power_raw * power_lsb
         print(f"Мощность: {power_actual:.3f} Вт")
        
         # Чтение температуры (DIETEMP)
         _, stdout, _ = client.exec_command(f'i2cget -y {slot} {addr} {reg_dietemp} w')
         temp_raw = parse_i2c_word(stdout.read().decode())
         # Сдвиг вправо на 4 бита обязателен, так как данные температуры в INA237 лежат в битах 15:4
         temp_raw_shifted = twos_comp(temp_raw >> 4, 12)
         temp_actual = temp_raw_shifted * dietemp_lsb
         print(f"Температура чипа: {temp_actual:.1f} °C")

finally:
    client.close()
    print("\nSSH-сессия закрыта.")

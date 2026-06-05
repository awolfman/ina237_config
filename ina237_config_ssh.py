#!/usr/bin/python3

# Скрипт с автоматическим определением Endianness и автоматическим удалением/добавлением сертификата SSH

import os
import sys
import socket
import time
import paramiko

# ==========================================
# 1. ПРОВЕРКА АРГУМЕНТОВ КОМАНДНОЙ СТРОКИ
# ==========================================
if len(sys.argv) < 5:
    print("Использование: script.py <host> <slot> <user> <password>")
    sys.exit(1)

host = sys.argv[1]
slot = sys.argv[2]       # Номер I2C шины
user = sys.argv[3]
password = sys.argv[4]

# ==========================================
# 2. МАТЕМАТИЧЕСКИЕ ФУНКЦИИ И НАСТРОЙКИ INA237
# ==========================================
def twos_comp(val, bits):
    """Вычисление дополнения до двух для знаковых чисел."""
    if (val & (1 << (bits - 1))) != 0:
        val = val - (1 << bits)
    return val

i2caddr_list = [0x4a, 0x4b, 0x4e, 0x4f]

# Регистры INA237
REG_SHUNT_CAL        = 0x02
REG_VBUS             = 0x05
REG_DIETEMP          = 0x06
REG_CURRENT          = 0x07
REG_POWER            = 0x08
REG_MANUFACTURER_ID  = 0x3E

# Расчет коэффициентов (LSB) согласно даташиту
max_current = 20        
shunt_resistance = 0.005 

current_lsb = max_current / 32768
vbus_lsb = 3.125 * 10**(-3)       
dietemp_lsb = 125 * 10**(-3)     
power_lsb = 0.2 * current_lsb

calibration_value = int((819.2 * 10**6) * current_lsb * shunt_resistance)
calibration_value &= 0x7FFF  

# ==========================================
# 3. SSH ПОДКЛЮЧЕНИЕ И УПРАВЛЕНИЕ КОМАНДАМИ
# ==========================================
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    print(f"Подключение к {host}...")
    client.connect(host, username=user, password=password, timeout=10)
except (socket.gaierror, socket.error, paramiko.SSHException) as e:
    print(f"Не удалось подключиться к {host}: {e}")
    sys.exit(1)

def ssh_run_cmd(cmd):
    """Выполняет команду на удаленном хосте. Возвращает (success, stdout_str)"""
    stdin, stdout, stderr = client.exec_command(cmd)
    error = stderr.read().decode().strip()
    output = stdout.read().decode().strip()
    if error:
        return False, error
    return True, output

def swap_bytes(val):
    """Меняет местами старший и младший байты в 16-битном слове"""
    low_byte = (val & 0xFF00) >> 8
    high_byte = (val & 0x00FF) << 8
    return high_byte | low_byte

# ==========================================
# 4. ЛОГИКА ИНИЦИАЛИЗАЦИИ И ПОРЯДКА БАЙТ
# ==========================================
def detect_endianness(slot, addr):
    """Автоматически определяет порядок байт утилиты i2cget."""
    cmd = f"i2cget -y {slot} {hex(addr)} {hex(REG_MANUFACTURER_ID)} w"
    success, res = ssh_run_cmd(cmd)
    if not success or not res:
        return None
    try:
        clean_res = "".join(char for char in res if char.isalnum())
        val = int(clean_res, 16)
        if val == 0x5449:
            print(f"[Автоопределение] Датчик {hex(addr)} вернул 0x5449. Режим: Big-Endian.")
            return 'big'
        elif val == 0x4954:
            print(f"[Автоопределение] Датчик {hex(addr)} вернул 0x4954. Режим: Little-Endian (нужен swap).")
            return 'little'
        else:
            print(f"[Предупреждение] Неизвестный ID производителя: {hex(val)} на адресе {hex(addr)}.")
            return None
    except ValueError:
        return None

def read_i2c_word(slot, addr, reg, endianness):
    """Читает 16-битное слово с учетом порядка байт."""
    cmd = f"i2cget -y {slot} {hex(addr)} {hex(reg)} w"
    success, res = ssh_run_cmd(cmd)
    if not success or not res:
        return None
    try:
        clean_res = "".join(char for char in res if char.isalnum())
        val = int(clean_res, 16)
        return swap_bytes(val) if endianness == 'little' else val
    except ValueError:
        return None

def write_i2c_word(slot, addr, reg, val, endianness):
    """Записывает 16-битное слово с учетом порядка байт."""
    swapped_val = swap_bytes(val) if endianness == 'little' else val
    cmd = f"i2cset -y {slot} {hex(addr)} {hex(reg)} {hex(swapped_val)} w"
    ssh_run_cmd(cmd)

# Главный процесс выполнения
try:
    # Автоопределение порядка байт по первому доступному датчику
    detected_endianness = None
    for addr in i2caddr_list:
        detected_endianness = detect_endianness(slot, addr)
        if detected_endianness:
            break
            
    if not detected_endianness:
        print("Ошибка: Не удалось определить порядок байт. Ни один датчик INA237 не ответил корректно.")
        sys.exit(1)

    # ==========================================
    # 5. ОСНОВНОЙ ЦИКЛ ОПРОСА ДАТЧИКОВ
    # ==========================================
    for addr in i2caddr_list:
        print(f"\n--- Опрос датчика по адресу {hex(addr)} ---")
        
        # Запись калибровочного значения
        write_i2c_word(slot, addr, REG_SHUNT_CAL, calibration_value, detected_endianness)
        
        # Даем АЦП ПЛИС/INA237 время обновить регистры после новой калибровки
        time.sleep(0.05)
        
        # Чтение регистров
        vbus_raw = read_i2c_word(slot, addr, REG_VBUS, detected_endianness)
        current_raw = read_i2c_word(slot, addr, REG_CURRENT, detected_endianness)
        power_raw = read_i2c_word(slot, addr, REG_POWER, detected_endianness)
        temp_raw = read_i2c_word(slot, addr, REG_DIETEMP, detected_endianness)
        
        # Строгая проверка: если хоть один регистр вернул None, датчик работает некорректно
        if any(v is None for v in [vbus_raw, current_raw, power_raw, temp_raw]):
            print(f"Ошибка: Датчик {hex(addr)} вернул некорректные или пустые данные.")
            continue
            
        # Преобразование данных согласно даташиту INA237
        # ИСПРАВЛЕНО: VBUS в INA237 — чистый 16-битный регистр, сдвиг на 4 бита не требуется!
        vbus = vbus_raw * vbus_lsb
        
        # Температура: используются только старшие 12 бит из 16, число со знаком
        temp_sign = twos_comp(temp_raw >> 4, 12)
        temp = temp_sign * dietemp_lsb
        
        # Ток: полноразмерное 16-битное число со знаком
        current_sign = twos_comp(current_raw, 16)
        current = current_sign * current_lsb
        
        # Мощность: полноразмерное 16-битное беззнаковое число
        power = power_raw * power_lsb
        
        # Вывод результатов для инженера
        print(f"Напряжение шины: {vbus:.3f} В")
        print(f"Ток:             {current:.3f} А")
        print(f"Мощность:        {power:.3f} Вт")
        print(f"Температура чипа:{temp:.1f} °C")

finally:
    client.close()
    print("\nСоединение SSH закрыто.")

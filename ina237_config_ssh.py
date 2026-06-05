#!/usr/bin/python3

# Скрипт с автоматическим определением Endianness и автоматическим удалением/добавлением сертификата SSH

import os
import sys
import socket
import paramiko

# Проверка аргументов командной строки
if len(sys.argv) < 5:
    print("Использование: script.py <host> <slot> <user> <password>")
    sys.exit(1)

host = sys.argv[1]
slot = sys.argv[2]       # Номер I2C шины
user = sys.argv[3]
password = sys.argv[4]

def twos_comp(val, bits):
    """Вычисление дополнения до двух для знаковых чисел."""
    if (val & (1 << (bits - 1))) != 0:
        val = val - (1 << bits)
    return val

# Настройки для INA237
i2caddr_list = [0x4a, 0x4b, 0x4e, 0x4f]

# Регистры INA237
REG_SHUNT_CAL        = 0x02
REG_VBUS             = 0x05
REG_DIETEMP          = 0x06
REG_CURRENT          = 0x07
REG_POWER            = 0x08
REG_MANUFACTURER_ID  = 0x3E  # Значение по умолчанию: 0x5449 ("TI")

# Расчет коэффициентов (LSB)
max_current = 20        
shunt_resistance = 0.005 

current_lsb = max_current / 32768
vbus_lsb = 3.125 * 10**(-3)       
dietemp_lsb = 125 * 10**(-3)     
power_lsb = 0.2 * current_lsb

calibration_value = int((819.2 * 10**6) * current_lsb * shunt_resistance)
calibration_value &= 0x7FFF  

# Проверка наличия и удаления ssh-ключа конкретного хоста с бэкапом и поддержкой хеширования

# Пути к файлам
hosts_file = os.path.expanduser('~/.ssh/known_hosts')
backup_file = f"{hosts_file}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.bak"

# Загружаем текущие ключи
if not os.path.exists(hosts_file):
    print(f"Ошибка: файл {hosts_file} не найден.", file=sys.stderr)
    sys.exit(1)

host_keys = paramiko.HostKeys(filename=hosts_file)

# Ищем записи, соответствующие хосту (включая хэшированные)
matched_entries = host_keys.lookup(host)

if matched_entries:
    # Создаем временный бэкап перед изменениями
    try:
        shutil.copy2(hosts_file, backup_file)
        print(f"Создан временный бэкап: {backup_file}")
    except Exception as e:
        print(f"Критическая ошибка: не удалось создать бэкап. Изменения не внесены. {e}", file=sys.stderr)
        sys.exit(1)

    # Фильтруем записи, удаляя те, которые соответствуют нашему хосту
    keys_to_remove = list(matched_entries.keys())
    host_keys._entries = [
        entry for entry in host_keys._entries 
        if not (host_keys.check(host, entry.key) and entry.key.get_name() in keys_to_remove)
    ]

    # Безопасное сохранение изменений и удаление бэкапа
    try:
        # Пытаемся сохранить файл
        host_keys.save(hosts_file)
        print(f"Ключи для хоста {host} успешно удалены из {hosts_file}")
        
        # Если сохранение прошло успешно — удаляем бэкап
        if os.path.exists(backup_file):
            os.remove(backup_file)
            print("Временный бэкап успешно удален.")
            
    except PermissionError:
        print(f"Ошибка доступа: нет прав на запись в файл {hosts_file}. Бэкап сохранен в {backup_file}", file=sys.stderr)
    except Exception as e:
        print(f"Не удалось сохранить файл {hosts_file}. Ошибка: {e}. Бэкап сохранен в {backup_file}", file=sys.stderr)
else:
    print(f"Хост {host} не найден в {hosts_file} (проверены явные и хэшированные записи)")

# Подключение по SSH
client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

try:
    print(f"Подключение к {host}...")
    client.connect(host, username=user, password=password, timeout=10)
except (socket.gaierror, socket.error, paramiko.SSHException) as e:
    print(f"Не удалось подключиться к {host}: {e}")
    sys.exit(1)

def ssh_run_cmd(cmd):
    """Выполняет команду на удаленном хосте"""
    stdin, stdout, stderr = client.exec_command(cmd)
    error = stderr.read().decode().strip()
    if error:
        return None
    return stdout.read().decode().strip()

def swap_bytes(val):
    """Меняет местами старший и младший байты в 16-битном слове"""
    low_byte = (val & 0xFF00) >> 8
    high_byte = (val & 0x00FF) << 8
    return high_byte | low_byte

def detect_endianness(slot, addr):
    """
    Автоматически определяет порядок байт утилиты i2cget.
    Читает REG_MANUFACTURER_ID (должен быть 0x5449).
    """
    cmd = f"i2cget -y {slot} {hex(addr)} {hex(REG_MANUFACTURER_ID)} w"
    res = ssh_run_cmd(cmd)
    if not res:
        return None
    try:
        val = int(res, 16)
        if val == 0x5449:
            print(f"[Автоопределение] Датчик {hex(addr)} вернул 0x5449. Режим: Big-Endian (без инверсии байт).")
            return 'big'
        elif val == 0x4954:
            print(f"[Автоопределение] Датчик {hex(addr)} вернул 0x4954. Режим: Little-Endian (требуется инверсия байт).")
            return 'little'
        else:
            print(f"[Предупреждение] Неизвестный ID производителя: {hex(val)} на адресе {hex(addr)}.")
            return None
    except ValueError:
        return None

def read_i2c_word(slot, addr, reg, endianness):
    """Читает 16-битное слово с учетом порядка байт."""
    cmd = f"i2cget -y {slot} {hex(addr)} {hex(reg)} w"
    res = ssh_run_cmd(cmd)
    if not res:
        return None
    try:
        val = int(res, 16)
        return swap_bytes(val) if endianness == 'little' else val
    except ValueError:
        return None

def write_i2c_word(slot, addr, reg, val, endianness):
    """Записывает 16-битное слово с учетом порядка байт."""
    swapped_val = swap_bytes(val) if endianness == 'little' else val
    cmd = f"i2cset -y {slot} {hex(addr)} {hex(reg)} {hex(swapped_val)} w"
    ssh_run_cmd(cmd)

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

    # Цикл опроса датчиков с использованием определенного режима
    for addr in i2caddr_list:
        print(f"\n--- Опрос датчика по адресу {hex(addr)} ---")
        
        # Запись калибровочного значения
        write_i2c_word(slot, addr, REG_SHUNT_CAL, calibration_value, detected_endianness)
        
        # Чтение регистров
        vbus_raw = read_i2c_word(slot, addr, REG_VBUS, detected_endianness)
        current_raw = read_i2c_word(slot, addr, REG_CURRENT, detected_endianness)
        power_raw = read_i2c_word(slot, addr, REG_POWER, detected_endianness)
        temp_raw = read_i2c_word(slot, addr, REG_DIETEMP, detected_endianness)
        
        if vbus_raw is None:
            print(f"Ошибка: датчик {hex(addr)} не отвечает.")
            continue
            
        # Преобразование данных согласно даташиту INA237
        vbus = (vbus_raw >> 4) * vbus_lsb
        
        temp_sign = twos_comp(temp_raw >> 4, 12)
        temp = temp_sign * dietemp_lsb
        
        current_sign = twos_comp(current_raw, 16)
        current = current_sign * current_lsb
        
        power = power_raw * power_lsb
        
        # Вывод результатов
        print(f"Напряжение шины: {vbus:.3f} В")
        print(f"Ток:             {current:.3f} А")
        print(f"Мощность:        {power:.3f} Вт")
        print(f"Температура чипа:{temp:.1f} °C")

finally:
    client.close()
    print("\nСоединение SSH закрыто.")

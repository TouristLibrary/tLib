# Настройка статического IP для Ubuntu VM

Виртуальная машина может получать разные IP-адреса после перезагрузки.
Эта инструкция фиксирует текущий IP как статический.

## Быстрая настройка (однострочник)

Подключитесь к VM и выполните:

```bash
IP=$(ip -4 addr show $(ip route | grep default | awk '{print $5}') | grep -oP '(?<=inet\s)\d+(\.\d+){3}/\d+') && GW=$(ip route | grep default | awk '{print $3}') && IF=$(ip route | grep default | awk '{print $5}') && echo "network: {version: 2, ethernets: {$IF: {addresses: [$IP], routes: [{to: default, via: $GW}], nameservers: {addresses: [8.8.8.8, 8.8.4.4]}}}}" | sudo tee /etc/netplan/99-static.yaml && sudo chmod 600 /etc/netplan/99-static.yaml && sudo netplan apply && echo "Static IP set: ${IP%/*}"
```

После выполнения будет выведен IP-адрес для SSH-подключения.

## Откат изменений

Если что-то пошло не так:

```bash
sudo rm /etc/netplan/99-static.yaml && sudo netplan apply
```

## Проверка настроек

```bash
cat /etc/netplan/99-static.yaml
ip addr show
```

"""Patch the existing protected initrd for explicit, bounded installations.

With no installed marker, including malformed or incomplete boot arguments,
the ISO-only read-only boot and tmpfs DATA protection remains the default.
The installed path requires unique boot/data UUIDs on the same physical block
device and checks the native disk.uuid before permitting DATA to be mounted.
"""
import gzip

from protect_live_initrd import records, SAFE_BOOT, SAFE_DATA


NEW_BOOT=b'''# The installed branch is opt-in; live wins even if stale boot flags exist.
is_installed_boot() {
    case " $(cat /proc/cmdline) " in *" live "*) return 1 ;; esac
    case " $(cat /proc/cmdline) " in *" daphile_installed=1 "*) return 0 ;; esac
    return 1
}

unique_uuid_partition() {
    local wanted="$1" node name device value found="" count=0
    for node in /sys/class/block/*; do
        [[ -f "${node}/partition" ]] || continue
        name=${node##*/}; device="/dev/${name}"
        [[ -b "$device" ]] || continue
        value=$(blkid "$device" 2>/dev/null | sed -n 's/.* UUID="\\([^"]*\\)".*/\\1/p')
        if [[ "$value" = "$wanted" ]]; then found="$device"; count=$((count+1)); fi
    done
    [[ "$count" -eq 1 ]] || return 1
    printf '%s' "$found"
}

same_parent_device() {
    [[ -n "$1" && -n "$2" && "${1%/*}" = "${2%/*}" ]]
}

mount_boot() {
    local dev devices fs bootnode datanode bootsys datasys uuidfile
    INSTALLED_DATA=""
    if is_installed_boot; then
        # Positive marker AND both uniquely identified partitions are needed.
        case "${boot_uuid}" in ''|*[!0-9A-Fa-f-]*) return 1 ;; esac
        case "${data_uuid}" in ''|*[!0-9A-Fa-f-]*) return 1 ;; esac
        [[ ${#boot_uuid} -eq 36 && ${#data_uuid} -eq 36 && "$boot_uuid" != "$data_uuid" ]] || return 1
        # USB devices may enumerate after init starts, as in the stock loop.
        bootnode=""; datanode=""
        for timeout in $(seq 50); do
            bootnode=$(unique_uuid_partition "$boot_uuid") || bootnode=""
            datanode=$(unique_uuid_partition "$data_uuid") || datanode=""
            [[ -n "$bootnode" && -n "$datanode" ]] && break
            sleep 0.1
        done
        [[ -n "$bootnode" && -n "$datanode" ]] || return 1
        bootsys=$(readlink -f "/sys/class/block/${bootnode##*/}") || return 1
        datasys=$(readlink -f "/sys/class/block/${datanode##*/}") || return 1
        same_parent_device "$bootsys" "$datasys" || return 1
        fs=$(blkid "$bootnode" 2>/dev/null | sed 's/.* TYPE="\\([^"]*\\)".*/\\1/')
        [[ "$fs" = ext4 ]] || return 1
        mount -n -t ext4 -o ro,noload "$bootnode" /boot 2>/dev/null || return 1
        uuidfile=$(cat /boot/disk.uuid 2>/dev/null)
        if [[ "$uuidfile" != "boot_uuid=$boot_uuid data_uuid=$data_uuid" || ! -f "/boot/boot/${daphile}/rootfs" ]]; then
            umount -ln /boot
            return 1
        fi
        mount -n -o remount,rw,noatime /boot 2>/dev/null || { umount -ln /boot; return 1; }
        INSTALLED_DATA="$datanode"
        return 0
    fi
    # Absent installed marker: an ISO may be read; no persistent DATA is used.
    for timeout in $(seq 50); do
        devices=$(find /dev/ -type b)
        for dev in ${devices}; do
            [[ -b "$dev" && ! "${dev##*/}" =~ ^loop[0-9]+$ ]] || continue
            fs=$(blkid "$dev" 2>/dev/null | sed 's/.* TYPE="\\([^"]*\\)".*/\\1/')
            [[ "$fs" = iso9660 ]] || continue
            mount -n -t iso9660 -o ro "$dev" /boot 2>/dev/null || continue
            [[ -f "/boot/boot/${daphile}/rootfs" && -e /boot/boot/live ]] && return 0
            umount -ln /boot
        done
        sleep 0.1
    done
    return 1
}'''

NEW_DATA=b'''mount_data() {
    # INSTALLED_DATA is set only after the boot partition and binding validate.
    if [[ -n "${INSTALLED_DATA}" ]]; then
        mount_dev "${INSTALLED_DATA}" "$1" || rescue_shell
    else
        mount_dev none "$1" || rescue_shell
    fi
}'''


def patch_initrd(raw):
    first=list(records(raw));offset=(first[-1][4]+511)&~511
    if raw[offset:offset+3]!=b'\x1f\x8b\x08':raise ValueError('unexpected compressed initrd')
    old=gzip.decompress(raw[offset:]);rows=list(records(old));out=bytearray();changed=0
    for name,start,pos,size,end in rows:
        if name!='init':out+=old[start:end];continue
        init=old[pos:pos+size]
        if init.count(SAFE_BOOT)!=1 or init.count(SAFE_DATA)!=1:raise ValueError('unexpected protected init')
        init=init.replace(SAFE_BOOT,NEW_BOOT).replace(SAFE_DATA,NEW_DATA)
        header=bytearray(old[start:start+110]);header[54:62]=('%08x'%len(init)).encode('ascii')
        out+=header;out+=old[start+110:pos];out+=init;out+=b'\0'*((-len(out))%4);changed+=1
    if changed!=1:raise ValueError('init must be unique')
    out+=old[rows[-1][4]:]
    result=raw[:offset]+gzip.compress(bytes(out),compresslevel=9,mtime=0)
    if gzip.decompress(result[offset:])!=bytes(out):raise ValueError('initrd readback mismatch')
    return result

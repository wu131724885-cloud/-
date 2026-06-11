# uncompyle6 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.8.20 (default, Oct  3 2024, 15:19:54) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: esptool\cmds.py
import hashlib, io, os, struct, sys, time, zlib
from intelhex import IntelHex
from .bin_image import ELFFile, ImageSegment, LoadFirmwareImage
from .bin_image import ESP8266ROMFirmwareImage, ESP8266V2FirmwareImage, ESP8266V3FirmwareImage
from .loader import DEFAULT_CONNECT_ATTEMPTS, DEFAULT_TIMEOUT, ERASE_WRITE_TIMEOUT_PER_MB, ESPLoader, timeout_per_mb
from .targets import CHIP_DEFS, CHIP_LIST, ROM_LIST
from .uf2_writer import UF2Writer
from .util import FatalError, NotImplementedInROMError, NotSupportedError, UnsupportedCommandError
from .util import div_roundup, flash_size_bytes, get_file_size, hexify, pad_to, print_overwrite
DETECTED_FLASH_SIZES = {
 18: '"256KB"', 
 19: '"512KB"', 
 20: '"1MB"', 
 21: '"2MB"', 
 22: '"4MB"', 
 23: '"8MB"', 
 24: '"16MB"', 
 25: '"32MB"', 
 26: '"64MB"', 
 27: '"128MB"', 
 28: '"256MB"', 
 32: '"64MB"', 
 33: '"128MB"', 
 34: '"256MB"', 
 50: '"256KB"', 
 51: '"512KB"', 
 52: '"1MB"', 
 53: '"2MB"', 
 54: '"4MB"', 
 55: '"8MB"', 
 56: '"16MB"', 
 57: '"32MB"', 
 58: '"64MB"'}
FLASH_MODES = {
 'qio': 0, 'qout': 1, 'dio': 2, 'dout': 3}

def detect_chipParse error at or near `LOAD_CONST' instruction at offset 0


def load_ram(esp, args):
    image = LoadFirmwareImageesp.CHIP_NAMEargs.filename
    print("RAM boot...")
    for seg in image.segments:
        size = len(seg.data)
        print(("Downloading %d bytes at %08x..." % (size, seg.addr)), end=" ")
        sys.stdout.flush
        esp.mem_begin(size, div_roundupsizeesp.ESP_RAM_BLOCK, esp.ESP_RAM_BLOCK, seg.addr)
        seq = 0
        if len(seg.data) > 0:
            esp.mem_block(seg.data[0[:esp.ESP_RAM_BLOCK]], seq)
            seg.data = seg.data[esp.ESP_RAM_BLOCK[:None]]
            seq += 1
        else:
            print("done!")
    else:
        print("All segments done, executing at %08x" % image.entrypoint)
        esp.mem_finishimage.entrypoint


def read_mem(esp, args):
    print("0x%08x = 0x%08x" % (args.address, esp.read_regargs.address))


def write_mem(esp, args):
    esp.write_reg(args.address, args.value, args.mask, 0)
    print("Wrote %08x, mask %08x to %08x" % (args.value, args.mask, args.address))


def dump_mem(esp, args):
    with openargs.filename"wb" as f:
        for i in range(args.size // 4):
            d = esp.read_reg(args.address + i * 4)
            f.writestruct.pack(b'<I', d)
            if f.tell % 1024 == 0:
                print_overwrite("%d bytes read... (%d %%)" % (f.tell, f.tell * 100 // args.size))
            sys.stdout.flush
        else:
            print_overwrite(("Read %d bytes" % f.tell), last_line=True)

    print("Done!")


def detect_flash_size(esp, args=None):
    if esp.secure_download_mode:
        if args is not None and args.flash_size == "detect":
            raise FatalError("Detecting flash size is not supported in secure download mode. Need to manually specify flash size.")
        else:
            return
    else:
        flash_id = esp.flash_id
        size_id = flash_id >> 16
        flash_size = DETECTED_FLASH_SIZES.getsize_id
        if args is not None and args.flash_size == "detect":
            if flash_size is None:
                flash_size = "4MB"
                print(f"Warning: Could not auto-detect Flash size (FlashID={flash_id:#x}, SizeID={size_id:#x}), defaulting to 4MB")
            else:
                print"Auto-detected Flash size:"flash_size
            args.flash_size = flash_size
    return flash_size


def _update_image_flash_params(esp, address, args, image):
    """
    Modify the flash mode & size bytes if this looks like an executable bootloader image
    """
    if len(image) < 8:
        return image
    magic, _, flash_mode, flash_size_freq = struct.unpack("BBBB", image[None[:4]])
    if address != esp.BOOTLOADER_FLASH_OFFSET:
        return image
    if (args.flash_mode, args.flash_freq, args.flash_size) == ('keep', 'keep', 'keep'):
        return image
    if magic != esp.ESP_IMAGE_MAGIC:
        print("Warning: Image file at 0x%x doesn't look like an image file, so not changing any flash settings." % address)
        return image
    try:
        test_image = esp.BOOTLOADER_IMAGEio.BytesIOimage
        test_image.verify
    except Exception:
        print("Warning: Image file at 0x%x is not a valid %s image, so not changing any flash settings." % (
         address, esp.CHIP_NAME))
        return         return image
    else:
        sha_implies_keep = args.chip != "esp8266" and image[23] == 1

        def print_keep_warning(arg_to_keep, arg_used):
            print("Warning: Image file at {addr} is protected with a hash checksum, so not changing the flash {arg} setting. Use the --flash_{arg}=keep option instead of --flash_{arg}={arg_orig} in order to remove this warning, or use the --dont-append-digest option for the elf2image command in order to generate an image file without a hash checksum".format(addr=(hex(address)),
              arg=arg_to_keep,
              arg_orig=arg_used))

        if args.flash_mode != "keep":
            new_flash_mode = FLASH_MODES[args.flash_mode]
            if flash_mode != new_flash_mode and sha_implies_keep:
                print_keep_warning"mode"args.flash_mode
            else:
                flash_mode = new_flash_mode
        flash_freq = flash_size_freq & 15
        if args.flash_freq != "keep":
            new_flash_freq = esp.parse_flash_freq_argargs.flash_freq
            if flash_freq != new_flash_freq and sha_implies_keep:
                print_keep_warning"frequency"args.flash_freq
            else:
                flash_freq = new_flash_freq
        flash_size = flash_size_freq & 240
        if args.flash_size != "keep":
            new_flash_size = esp.parse_flash_size_argargs.flash_size
            if flash_size != new_flash_size and sha_implies_keep:
                print_keep_warning"size"args.flash_size
            else:
                flash_size = new_flash_size
        flash_params = struct.pack(b'BB', flash_mode, flash_size + flash_freq)
        if flash_params != image[2[:4]]:
            print("Flash params set to 0x%04x" % struct.unpack(">H", flash_params))
            image = image[0[:2]] + flash_params + image[4[:None]]
        return image


def write_flashParse error at or near `LOAD_DEREF' instruction at offset 0


def image_infoParse error at or near `LOAD_CLOSURE' instruction at offset 0


def make_image(args):
    print("Creating {} image...".formatargs.chip)
    image = ESP8266ROMFirmwareImage
    if len(args.segfile) == 0:
        raise FatalError("No segments specified")
    if len(args.segfile) != len(args.segaddr):
        raise FatalError("Number of specified files does not match number of specified addresses")
    for seg, addr in zipargs.segfileargs.segaddr:
        with openseg"rb" as f:
            data = f.read
            image.segments.appendImageSegmentaddrdata
    else:
        image.entrypoint = args.entrypoint
        image.saveargs.output
        print("Successfully created {} image.".formatargs.chip)


def elf2image(args):
    e = ELFFile(args.input)
    if args.chip == "auto":
        args.chip = "esp8266"
    else:
        print("Creating {} image...".formatargs.chip)
        if args.ram_only_header:
            print("ROM segments hidden - only RAM segments are visible to the ROM loader!")
        elif args.chip != "esp8266":
            image = CHIP_DEFS[args.chip].BOOTLOADER_IMAGE
            if args.chip == "esp32":
                if args.secure_pad:
                    image.secure_pad = "1"
            if args.secure_pad_v2:
                image.secure_pad = "2"
            image.min_rev = args.min_rev
            image.min_rev_full = args.min_rev_full
            image.max_rev_full = args.max_rev_full
            image.ram_only_header = args.ram_only_header
            image.append_digest = args.append_digest
        else:
            if args.version == "1":
                image = ESP8266ROMFirmwareImage
            else:
                if args.version == "2":
                    image = ESP8266V2FirmwareImage
                else:
                    image = ESP8266V3FirmwareImage
    image.entrypoint = e.entrypoint
    image.flash_mode = FLASH_MODES[args.flash_mode]
    if args.flash_mmu_page_size:
        image.set_mmu_page_sizeflash_size_bytes(args.flash_mmu_page_size)
    image.segments = e.segments if args.use_segments else e.sections
    if args.pad_to_size:
        image.pad_to_size = flash_size_bytes(args.pad_to_size)
    image.flash_size_freq = image.ROM_LOADER.parse_flash_size_argargs.flash_size
    image.flash_size_freq += image.ROM_LOADER.parse_flash_freq_argargs.flash_freq
    if args.elf_sha256_offset:
        image.elf_sha256 = e.sha256
        image.elf_sha256_offset = args.elf_sha256_offset
    before = len(image.segments)
    image.merge_adjacent_segments
    if len(image.segments) != before:
        delta = before - len(image.segments)
        print("Merged %d ELF section%s" % (delta, "s" if delta > 1 else ""))
    image.verify
    if args.output is None:
        args.output = image.default_output_nameargs.input
    image.saveargs.output
    print("Successfully created {} image.".formatargs.chip)


def read_mac(esp, args):

    def print_mac(label, mac):
        print("%s: %s" % (label, ":".joinmap(lambda x: "%02x" % x)mac))

    eui64 = esp.read_mac"EUI64"
    if eui64:
        print_mac"MAC"eui64
        print_mac"BASE MAC"esp.read_mac"BASE_MAC"
        print_mac"MAC_EXT"esp.read_mac"MAC_EXT"
    else:
        print_mac"MAC"esp.read_mac"BASE_MAC"


def chip_id(esp, args):
    try:
        chipid = esp.chip_id
        print("Chip ID: 0x%08x" % chipid)
    except NotSupportedError:
        print("Warning: %s has no Chip ID. Reading MAC instead." % esp.CHIP_NAME)
        read_macespargs


def erase_flash(esp, args):
    if not args.force:
        if esp.CHIP_NAME != "ESP8266":
            if not esp.secure_download_mode:
                if esp.get_flash_encryption_enabled or esp.get_secure_boot_enabled:
                    raise FatalError("Active security features detected, erasing flash is disabled as a safety measure. Use --force to override, please use with caution, otherwise it may brick your device!")
    print("Erasing flash (this may take a while)...")
    t = time.time
    esp.erase_flash
    print("Chip erase completed successfully in %.1fs" % (time.time - t))


def erase_region(esp, args):
    if not args.force:
        if esp.CHIP_NAME != "ESP8266":
            if not esp.secure_download_mode:
                if esp.get_flash_encryption_enabled or esp.get_secure_boot_enabled:
                    raise FatalError("Active security features detected, erasing flash is disabled as a safety measure. Use --force to override, please use with caution, otherwise it may brick your device!")
    print("Erasing region (may be slow depending on size)...")
    t = time.time
    esp.erase_region(args.address, args.size)
    print("Erase completed successfully in %.1f seconds." % (time.time - t))


def run(esp, args):
    esp.run


def flash_id(esp, args):
    flash_id = esp.flash_id
    print("Manufacturer: %02x" % (flash_id & 255))
    flid_lowbyte = flash_id >> 16 & 255
    print("Device: %02x%02x" % (flash_id >> 8 & 255, flid_lowbyte))
    print("Detected flash size: %s" % DETECTED_FLASH_SIZES.get(flid_lowbyte, "Unknown"))
    flash_type = esp.flash_type
    flash_type_dict = {0:"quad (4 data lines)",  1:"octal (8 data lines)"}
    flash_type_str = flash_type_dict.getflash_type
    if flash_type_str:
        print(f"Flash type set in eFuse: {flash_type_str}")


def read_flash(esp, args):
    if args.no_progress:
        flash_progress = None
    else:

        def flash_progress(progress, length):
            msg = "%d (%d %%)" % (progress, progress * 100.0 / length)
            padding = "\x08" * len(msg)
            if progress == length:
                padding = "\n"
            sys.stdout.write(msg + padding)
            sys.stdout.flush

    t = time.time
    data = esp.read_flash(args.address, args.size, flash_progress)
    t = time.time - t
    speed_msg = " ({:.1f} kbit/s)".format(len(data) / t * 8 / 1000) if t > 0.0 else ""
    print_overwrite(("Read {:d} bytes at {:#010x} in {:.1f} seconds{}...".format(len(data), args.address, t, speed_msg)),
      last_line=True)
    with openargs.filename"wb" as f:
        f.writedata


def verify_flashParse error at or near `LOAD_CONST' instruction at offset 0


def read_flash_status(esp, args):
    print("Status value: 0x%04x" % esp.read_statusargs.bytes)


def write_flash_status(esp, args):
    fmt = "0x%%0%dx" % (args.bytes * 2)
    args.value = args.value & (1 << args.bytes * 8) - 1
    print(("Initial flash status: " + fmt) % esp.read_statusargs.bytes)
    print(("Setting flash status: " + fmt) % args.value)
    esp.write_status(args.value, args.bytes, args.non_volatile)
    print(("After flash status:   " + fmt) % esp.read_statusargs.bytes)


SECURITY_INFO_FLAG_MAP = {
 'SECURE_BOOT_EN': 1, 
 'SECURE_BOOT_AGGRESSIVE_REVOKE': 2, 
 'SECURE_DOWNLOAD_ENABLE': 4, 
 'SECURE_BOOT_KEY_REVOKE0': 8, 
 'SECURE_BOOT_KEY_REVOKE1': 16, 
 'SECURE_BOOT_KEY_REVOKE2': 32, 
 'SOFT_DIS_JTAG': 64, 
 'HARD_DIS_JTAG': 128, 
 'DIS_USB': 256, 
 'DIS_DOWNLOAD_DCACHE': 512, 
 'DIS_DOWNLOAD_ICACHE': 1024}

def get_security_flag_statusParse error at or near `SETUP_FINALLY' instruction at offset 0


def get_security_info(esp, args):
    si = esp.get_security_info
    print
    title = "Security Information:"
    print(title)
    print("=" * len(title))
    print("Flags: {:#010x} ({})".format(si["flags"], bin(si["flags"])))
    print("Key Purposes: {}".formatsi["key_purposes"])
    if si["chip_id"] is not None:
        if si["api_version"] is not None:
            print("Chip ID: {}".formatsi["chip_id"])
            print("API Version: {}".formatsi["api_version"])
        else:
            flags = si["flags"]
            if get_security_flag_status"SECURE_BOOT_EN"flags:
                print("Secure Boot: Enabled")
                if get_security_flag_status"SECURE_BOOT_AGGRESSIVE_REVOKE"flags:
                    print("Secure Boot Aggressive key revocation: Enabled")
                revoked_keys = []
                for i, key in enumerate([
                 "SECURE_BOOT_KEY_REVOKE0",
                 "SECURE_BOOT_KEY_REVOKE1",
                 "SECURE_BOOT_KEY_REVOKE2"]):
                    if get_security_flag_statuskeyflags:
                        revoked_keys.appendi

                if len(revoked_keys) > 0:
                    print("Secure Boot Key Revocation Status:\n")
                    for i in revoked_keys:
                        print(f"\tSecure Boot Key{i} is Revoked\n")

            else:
                print("Secure Boot: Disabled")
    else:
        flash_crypt_cnt = bin(si["flash_crypt_cnt"])
        if flash_crypt_cnt.count"1" % 2 != 0:
            print("Flash Encryption: Enabled")
        else:
            print("Flash Encryption: Disabled")
    CRYPT_CNT_STRING = "SPI Boot Crypt Count (SPI_BOOT_CRYPT_CNT)"
    if esp.CHIP_NAME == "esp32":
        CRYPT_CNT_STRING = "Flash Crypt Count (FLASH_CRYPT_CNT)"
    print(f'{CRYPT_CNT_STRING}: {si["flash_crypt_cnt"]:#x}')
    if get_security_flag_status"DIS_DOWNLOAD_DCACHE"flags:
        print("Dcache in UART download mode: Disabled")
    if get_security_flag_status"DIS_DOWNLOAD_ICACHE"flags:
        print("Icache in UART download mode: Disabled")
    hard_dis_jtag = get_security_flag_status"HARD_DIS_JTAG"flags
    soft_dis_jtag = get_security_flag_status"SOFT_DIS_JTAG"flags
    if hard_dis_jtag:
        print("JTAG: Permenantly Disabled")
    else:
        if soft_dis_jtag:
            print("JTAG: Software Access Disabled")
        if get_security_flag_status"DIS_USB"flags:
            print("USB Access: Disabled")


def merge_bin(args):
    try:
        chip_class = CHIP_DEFS[args.chip]
    except KeyError:
        msg = "Please specify the chip argument" if args.chip == "auto" else f"Invalid chip choice: '{args.chip}'"
        msg = f'{msg} (choose from {", ".joinCHIP_LIST})'
        raise FatalError(msg)
    else:
        input_files = sorted((args.addr_filename), key=(lambda x: x[0]))
        if not input_files:
            raise FatalError("No input files specified")
        first_addr = input_files[0][0]
        if first_addr < args.target_offset:
            raise FatalError(f"Output file target offset is {args.target_offset:#x}. Input file offset {first_addr:#x} is before this.")
        if args.format == "uf2":
            with UF2Writer((chip_class.UF2_FAMILY_ID),
              (args.output),
              (args.chunk_size),
              md5_enabled=(not args.md5_disable)) as writer:
                for addr, argfile in input_files:
                    print(f"Adding {argfile.name} at {addr:#x}")
                    image = argfile.read
                    image = _update_image_flash_paramschip_classaddrargsimage
                    writer.add_file(addr, image)

            print(f"Wrote {os.path.getsizeargs.output:#x} bytes to file {args.output}, ready to be flashed with any ESP USB Bridge")
        elif args.format == "raw":
            with openargs.output"wb" as of:

                def pad_to(flash_offs):
                    of.write(b'\xff' * (flash_offs - args.target_offset - of.tell))

                for addr, argfile in input_files:
                    pad_to(addr)
                    image = argfile.read
                    image = _update_image_flash_paramschip_classaddrargsimage
                    of.writeimage
                else:
                    if args.fill_flash_size:
                        pad_to(flash_size_bytes(args.fill_flash_size))
                    print(f"Wrote {of.tell:#x} bytes to file {args.output}, ready to flash to offset {args.target_offset:#x}")

        else:
            if args.format == "hex":
                out = IntelHex
                for addr, argfile in input_files:
                    ihex = IntelHex
                    image = argfile.read
                    image = _update_image_flash_paramschip_classaddrargsimage
                    ihex.frombytes(image, addr)
                    out.mergeihex
                else:
                    out.write_hex_fileargs.output
                    print(f"Wrote {os.path.getsizeargs.output:#x} bytes to file {args.output}, ready to flash to offset {args.target_offset:#x}")


def version(args):
    from . import __version__
    print(__version__)
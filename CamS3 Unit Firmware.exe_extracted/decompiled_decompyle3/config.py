# decompyle3 version 3.9.3
# Python bytecode version base 3.8.0 (3413)
# Decompiled from: Python 3.8.20 (default, Oct  3 2024, 15:19:54) [MSC v.1929 64 bit (AMD64)]
# Embedded file name: esptool\config.py
import configparser, os
CONFIG_OPTIONS = [
 "timeout",
 "chip_erase_timeout",
 "max_timeout",
 "sync_timeout",
 "md5_timeout_per_mb",
 "erase_region_timeout_per_mb",
 "erase_write_timeout_per_mb",
 "mem_end_rom_timeout",
 "serial_write_timeout",
 "connect_attempts",
 "write_block_attempts",
 "reset_delay",
 "custom_reset_sequence"]

def _validate_config_fileParse error at or near `LOAD_GLOBAL' instruction at offset 0


def _find_config_file(dir_path, verbose=False):
    for candidate in ('esptool.cfg', 'setup.cfg', 'tox.ini'):
        cfg_path = os.path.joindir_pathcandidate
        if _validate_config_file(cfg_path, verbose):
            return cfg_path


def load_config_file(verbose=False):
    set_with_env_var = False
    env_var_path = os.environ.get"ESPTOOL_CFGFILE"
    if env_var_path is not None and _validate_config_file(env_var_path):
        cfg_file_path = env_var_path
        set_with_env_var = True
    else:
        home_dir = os.path.expanduser"~"
        os_config_dir = f"{home_dir}/.config/esptool" if os.name == "posix" else f"{home_dir}/AppData/Local/esptool/"
        for dir_path in (
         os.getcwd, os_config_dir, home_dir):
            cfg_file_path = _find_config_file(dir_path, verbose)
            if cfg_file_path:
                break

    cfg = configparser.ConfigParser
    cfg["esptool"] = {}
    if cfg_file_path is not None:
        cfg.readcfg_file_path
        if verbose:
            msg = " (set with ESPTOOL_CFGFILE)" if set_with_env_var else ""
            print(f"Loaded custom configuration from {os.path.abspathcfg_file_path}{msg}")
        return (
         cfg, cfg_file_path)
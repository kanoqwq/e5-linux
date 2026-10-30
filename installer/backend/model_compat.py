"""Explicit model exceptions, bound to one package/device/operation."""
import hashlib
import json
import re
import secrets
import time

BOARDS = {'k1': 'ums9157_2h10', 'yuanhang41s': 'P720S11',
          'lym10k': 'RY5G', 'changxing60': 'P720S20'}


def android_warning(profile, model, board):
    expected_board = BOARDS.get(profile['profile'])
    if model in profile['android_models'] and (not expected_board or board == expected_board):
        return None
    return {'expected_profile': profile['profile'],
            'expected_models': profile['android_models'], 'expected_board': expected_board,
            'actual_model': model, 'actual_board': board}


def runtime_warning(expected, actual):
    if expected == actual:
        return None
    return {'expected_profile': expected, 'expected_models': [expected],
            'expected_board': None, 'actual_model': actual, 'actual_board': None}


MU300_MODELS = ('MU300','F50','V50','U30 Air')


def mu300_android(model, board, platform=''):
    names = [name.strip().upper() for name in (model, board)]
    profile = 'mu300-family'
    expected_board = None
    matched = False
    if 'MU3351' in names or 'V50' in names:
        profile = 'v50'
        expected_board = 'MU3351 / ums9620'
        matched = 'MU3351' in names and 'V50' in names and platform.strip() == 'ums9620'
    elif re.search(r'U30.?Air', model+'\n'+board, re.I):
        profile, matched = 'u30air', True
    elif re.search(r'MU300|F50', model+'\n'+board, re.I):
        profile, matched = 'f50', True
    warning = None if matched else {
        'expected_profile':'mu300-family','expected_models':list(MU300_MODELS),
        'expected_board':expected_board,'actual_model':model,'actual_board':board,
        'actual_platform':platform}
    return profile, warning


def mu300_runtime(actual):
    if actual in ('f50','v50','u30air'):
        return None
    return {'expected_profile':'mu300-family','expected_models':list(MU300_MODELS),
            'expected_board':None,'actual_model':actual.upper(),'actual_board':None}


def fingerprint(package, device, method):
    value = {'package': {k: package.get(k) for k in
                         ('adapter', 'profile', 'kernel', 'layout', 'payloads')},
             'device': {k: device.get(k) for k in
                        ('serial', 'cid', 'model', 'board', 'system', 'model_mismatch')},
             'method': method}
    if 'platform' in device:
        value['device']['platform'] = device['platform']
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def same_token(value, expected):
    return isinstance(value, str) and secrets.compare_digest(value.encode(), expected.encode())


def decision(package, device, method, params):
    warning = device.get('model_mismatch')
    if not warning:
        return None, None
    token = fingerprint(package, device, method)
    if (params.get('model_confirmation') == 'yes' and
            same_token(params.get('model_token'), token)):
        return None, {'fingerprint': token, 'confirmation': 'yes', 'confirmed_at': time.time()}
    return {'confirmation_required': 'model_mismatch', 'model_token': token,
            'warning': warning, 'serial_hint': device['serial'][-4:]}, None


def require_approval(record, actual):
    if not actual.get('model_mismatch'):
        return
    approval = record.get('model_override', {})
    wanted = fingerprint(record['package'], actual, record['choices']['method'])
    if (approval.get('confirmation') != 'yes' or
            not same_token(approval.get('fingerprint'), wanted)):
        raise ValueError('Model changed or confirmation is missing; review the plan again / '
                         '机型发生变化或尚未确认，请重新查看安装计划并输入 yes')

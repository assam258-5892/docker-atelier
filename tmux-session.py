#!/usr/bin/python3
# -- coding: utf-8 -*-

import sys
import os
import subprocess
import yaml

_tmux_debug_env = os.environ.get('TMUX_DEBUG')
tmux_debug = _tmux_debug_env is not None and len(_tmux_debug_env) > 0 and _tmux_debug_env != '0'

def load_config(yml_path):
    with open(yml_path, encoding='utf-8') as f:
        return yaml.safe_load(f)

def get_compose_services(compose_file='docker-full.yml'):
    try:
        cmd = ['docker', 'compose', '-f', compose_file, 'ps', '--services']
        if tmux_debug:
            print(' '.join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        services = set(result.stdout.strip().splitlines())
        return services
    except Exception as e:
        print('Error getting compose services:', e)
        raise

def filter_panes_by_services(windows, docker_services):
    filtered_windows = []
    for win in windows:
        filtered_panes = []
        for pane in win['panes']:
            svc = pane.get('service')
            if svc is None or svc in docker_services:
                filtered_panes.append(pane)
        if filtered_panes:
            win_copy = dict(win)
            win_copy['panes'] = filtered_panes
            filtered_windows.append(win_copy)
    return filtered_windows

def get_windows_from_config(config):
    docker_services = get_compose_services()
    return filter_panes_by_services(config['windows'], docker_services)

def get_existing_tmux_structure(session):
    result = []
    try:
        check = subprocess.run(['tmux', 'has-session', '-t', session], capture_output=True, text=True)
        if check.returncode != 0:
            return result
        win_out = subprocess.run(['tmux', 'list-windows', '-t', session, '-F', '#I:#W'], capture_output=True, text=True, check=True)
        for line in win_out.stdout.strip().splitlines():
            idx, name = line.split(':', 1)
            pane_out = subprocess.run(['tmux', 'list-panes', '-t', f'{session}:{idx}', '-F', '#P:#{pane_start_command}'], capture_output=True, text=True, check=True)
            def strip_quotes(s):
                s = s.strip()
                if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                    return s[1:-1].strip()
                return s
            panes = []
            for pline in pane_out.stdout.strip().splitlines():
                if ':' in pline:
                    idx_str, cmd = pline.split(':', 1)
                    panes.append({'command': strip_quotes(cmd)})
            result.append({'name': name, 'panes': panes})
    except Exception as e:
        print(e)
        raise
    return result

def mark_create_candidates(filtered_windows, existing_map):
    for win in filtered_windows:
        name = win['name']
        if name not in existing_map:
            win['create'] = True
        else:
            win['create'] = False
        existing_panes = existing_map.get(name, {}).get('panes', [])
        existing_cmds = set(p['command'] for p in existing_panes)
        for pane in win['panes']:
            if pane['command'] not in existing_cmds:
                pane['create'] = True
            else:
                pane['create'] = False

def mark_delete_candidates(existing, filtered_windows):
    filtered_names = [w['name'] for w in filtered_windows]
    for win in existing:
        name = win['name']
        if name not in filtered_names:
            win['delete'] = True
        else:
            win['delete'] = False
        # pane 단위 비교
        filtered_panes = next((w['panes'] for w in filtered_windows if w['name'] == name), [])
        filtered_cmds = set(p['command'] for p in filtered_panes)
        for pane in win['panes']:
            if pane['command'] not in filtered_cmds:
                pane['delete'] = True
            else:
                pane['delete'] = False

def tmux(*args):
    cmd = ['tmux'] + list(args)
    if tmux_debug:
        def quote_arg(arg):
            if ' ' in arg or '\t' in arg or '\n' in arg or '"' in arg or "'" in arg:
                return '"' + arg.replace('"', '\"') + '"'
            return arg
        print(' '.join(quote_arg(a) for a in cmd))
    return subprocess.run(cmd, check=False)

def tmux_new_session(session, window, command):
    tmux('new-session', '-d', '-s', session, '-n', window, command)

def tmux_new_window(session, window, command):
    tmux('new-window', '-t', session, '-n', window, command)

def tmux_split_window(session, window, command):
    tmux('split-window', '-t', f'{session}:{window}', command)
    tmux('select-layout', '-t', f'{session}:{window}', 'tiled')

def tmux_select_layout(session, window, layout):
    tmux('select-layout', '-t', f'{session}:{window}', layout)

def tmux_select_pane(session, window, idx):
    tmux('select-pane', '-t', f'{session}:{window}.{idx}')

def tmux_resize_pane(session, window, target, x, y):
    tmux('resize-pane', '-t', f'{session}:{window}.{target}', '-x', str(x), '-y', str(y))

def move_window_to_index(session, name, target_idx):
    try:
        win_out = subprocess.run(['tmux', 'list-windows', '-t', session, '-F', '#I:#W'], capture_output=True, text=True, check=True)
        name_to_idx = {line.split(':',1)[1]: int(line.split(':',1)[0]) for line in win_out.stdout.strip().splitlines()}
        cur_idx = name_to_idx.get(name)
        if cur_idx is not None and cur_idx != target_idx:
            tmux('move-window', '-s', f'{session}:{cur_idx}', '-t', f'{session}:{target_idx}')
    except Exception as e:
        print(e)
        raise

def move_pane_to_index(session, name, panes, idx, pane_cmd):
    try:
        pane_out = subprocess.run(['tmux', 'list-panes', '-t', f'{session}:{name}', '-F', '#P:#{pane_start_command}'], capture_output=True, text=True, check=True)
        cmd_to_idx = {}
        for pline in pane_out.stdout.strip().splitlines():
            if ':' in pline:
                pidx, cmd = pline.split(':', 1)
                cmd_to_idx[cmd.strip()] = int(pidx)
        cur_idx = cmd_to_idx.get(pane_cmd)
        if cur_idx is not None and cur_idx != idx:
            if idx == 0:
                tmux('move-pane', '-s', f'{session}:{name}.{cur_idx}', '-t', f'{session}:{name}.0')
            else:
                prev_cmd = panes[idx-1]['command']
                prev_idx = cmd_to_idx.get(prev_cmd)
                if prev_idx is not None:
                    tmux('move-pane', '-s', f'{session}:{name}.{cur_idx}', '-t', f'{session}:{name}.{prev_idx}')
            tmux_select_layout(session, name, 'tiled')
    except Exception as e:
        print(e)
        raise

def tmux_attach_session(session):
    tmux_detach = os.environ.get('TMUX_DETACH')
    if tmux_detach is not None and len(tmux_detach) > 0 and tmux_detach != '0':
        print(f"tmux attach -t {session}")
    else:
        os.execlp('tmux', 'tmux', 'attach-session', '-t', session)

def main(session):
    filtered_windows = [w for w in windows if w.get('panes') and len(w['panes']) > 0]
    if not filtered_windows:
        return
    existing = get_existing_tmux_structure(session)
    existing_map = {w['name']: w for w in existing}
    mark_create_candidates(filtered_windows, existing_map)
    mark_delete_candidates(existing, filtered_windows)

    if not existing:
        first = filtered_windows[0]
        panes = first['panes']
        tmux_new_session(session, first['name'], panes[0]['command'])
        for pane in panes[1:]:
            tmux_split_window(session, first['name'], pane['command'])
        tmux_select_layout(session, first['name'], first.get('layout', 'tiled'))
        if 'resize_panes' in first:
            for r in first['resize_panes']:
                tmux_resize_pane(session, first['name'], r['target'], r['x'], r['y'])
        for win in filtered_windows[1:]:
            panes = win['panes']
            tmux_new_window(session, win['name'], panes[0]['command'])
            for pane in panes[1:]:
                tmux_split_window(session, win['name'], pane['command'])
            tmux_select_layout(session, win['name'], win.get('layout', 'tiled'))
            if 'resize_panes' in win:
                for r in win['resize_panes']:
                    tmux_resize_pane(session, win['name'], r['target'], r['x'], r['y'])
    else:
        for win in existing:
            name = win['name']
            if win.get('delete'):
                tmux('kill-window', '-t', f'{session}:{name}')
                continue
            for idx, pane in reversed(list(enumerate(win['panes']))):
                if pane.get('delete'):
                    tmux('kill-pane', '-t', f'{session}:{name}.{idx}')
        for win in filtered_windows:
            name = win['name']
            panes = win['panes']
            if win.get('create'):
                idx = filtered_windows.index(win)
                if idx == 0:
                    tmux('new-window', '-t', f'{session}:0', '-n', name, panes[0]['command'])
                else:
                    tmux('new-window', '-a', '-t', f'{session}:{idx-1}', '-n', name, panes[0]['command'])
                for pane in panes[1:]:
                    tmux_split_window(session, name, pane['command'])
                tmux_select_layout(session, name, win.get('layout', 'tiled'))
                if 'resize_panes' in win:
                    for r in win['resize_panes']:
                        tmux_resize_pane(session, name, r['target'], r['x'], r['y'])
                continue
            else:
                idx = filtered_windows.index(win)
                move_window_to_index(session, name, idx)
            existing_panes = existing_map[name]['panes']
            for idx, pane in enumerate(panes):
                if pane.get('create'):
                    if idx == 0:
                        tmux('split-window', '-b', '-t', f'{session}:{name}.0', pane['command'])
                    else:
                        tmux('split-window', '-t', f'{session}:{name}.{idx-1}', pane['command'])
                    tmux_select_layout(session, name, 'tiled')
                else:
                    move_pane_to_index(session, name, panes, idx, pane['command'])
            tmux_select_layout(session, name, win.get('layout', 'tiled'))
            if 'resize_panes' in win:
                for r in win['resize_panes']:
                    tmux_resize_pane(session, name, r['target'], r['x'], r['y'])
    tmux('select-window', '-t', f'{session}:0')
    tmux_attach_session(session)

if __name__ == '__main__':
    yml_path = 'tmux-docker.yml'
    session = None
    if len(sys.argv) > 1:
        yml_path = sys.argv[1]
        if len(sys.argv) > 2:
            session = sys.argv[2]
    config = load_config(yml_path)
    if session is None:
        session = config['session']
    global windows
    windows = get_windows_from_config(config)
    main(session)

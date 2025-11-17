#!/usr/bin/env python3
"""
Git 커밋 기반 코드 커버리지 리포트 생성 도구 (수정된 라인만)

기존 gcov 결과 파일(.gcda, .gcno)을 활용하여 수정된 라인만 분석합니다.

사용법:
  ./gencov.py <commit>                 # 해당 커밋에서 수정된 라인만
  ./gencov.py <commit1>..<commit2>     # commit1과 commit2 사이에 수정된 라인만
  ./gencov.py --all                    # 전체 커버리지 (모든 라인)
  ./gencov.py -o outdir <commit>       # 출력 디렉토리 지정

옵션:
  -o, --output-dir DIR    출력 디렉토리 (기본값: coverage)
  -h, --help              도움말 표시

출력:
  - <output-dir>/html/           : 수정된 라인들의 커버리지 HTML 리포트
  - <output-dir>/untested.md     : 수정된 라인 중 테스트되지 않은 코드 체크리스트
"""

import sys
import os
import subprocess
import re
import argparse
from datetime import datetime
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Set, Tuple, Optional


class GitDiffParser:
    """Git diff를 파싱하여 변경된 라인 번호 추출"""

    @staticmethod
    def parse_diff_hunk_header(line: str) -> Optional[Tuple[int, int]]:
        """
        Diff hunk 헤더 파싱: @@ -old_start,old_count +new_start,new_count @@
        Returns: (new_start, new_count) 또는 None
        """
        match = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
        if match:
            new_start = int(match.group(1))
            new_count = int(match.group(2)) if match.group(2) else 1
            return (new_start, new_count)
        return None

    @staticmethod
    def get_changed_lines(workspace_dir: Path, commit_range: str) -> Dict[str, Set[int]]:
        """
        Git diff로 변경된 파일과 라인 번호 추출
        Returns: {파일경로: {라인번호 set}}
        """
        if commit_range == "--all":
            return {}  # 전체 모드는 필터링 안함

        # Git diff 명령어 구성
        if ".." in commit_range:
            # 범위: commit1..commit2
            git_cmd = ["git", "diff", "-U0", commit_range]
        else:
            # 단일 커밋
            git_cmd = ["git", "show", "-U0", "--format=", commit_range]

        result = subprocess.run(
            git_cmd,
            cwd=workspace_dir,
            capture_output=True
        )

        if result.returncode != 0:
            stderr = result.stderr.decode('utf-8', errors='replace')
            print(f"❌ Git diff failed: {stderr}", file=sys.stderr)
            sys.exit(1)

        # UTF-8 디코딩 (바이너리 파일 무시)
        stdout = result.stdout.decode('utf-8', errors='ignore')

        # Diff 파싱
        changed_lines = defaultdict(set)
        current_file = None

        for line in stdout.split('\n'):
            # 파일 경로 추출: diff --git a/path b/path
            if line.startswith('diff --git'):
                current_file = None  # 새 파일 시작 시 초기화
                match = re.search(r' b/(.+)$', line)
                if match:
                    file_path = match.group(1)
                    # .c, .h 파일만 처리
                    if file_path.endswith(('.c', '.h')):
                        abs_path = (workspace_dir / file_path).resolve()
                        if abs_path.exists():
                            current_file = str(abs_path)

            # Hunk 헤더: @@ -old +new @@
            elif line.startswith('@@') and current_file:
                hunk_info = GitDiffParser.parse_diff_hunk_header(line)
                if hunk_info:
                    new_start, new_count = hunk_info
                    # 변경된 라인 범위 추가
                    for line_num in range(new_start, new_start + new_count):
                        changed_lines[current_file].add(line_num)

        return dict(changed_lines)


class SourceParser:
    """소스 코드 파싱하여 함수 범위 찾기"""

    @staticmethod
    def find_function_ranges(source_file: Path) -> Dict[str, Tuple[int, int]]:
        """
        소스 파일에서 함수 정의와 범위 찾기
        Returns: {function_name: (start_line, end_line)}
        """
        if not source_file.exists():
            return {}

        function_ranges = {}
        try:
            with open(source_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()

            current_func = None
            func_start = 0
            brace_depth = 0
            in_function = False
            recent_lines = []  # 최근 몇 줄 저장 (함수 정의가 여러 줄일 수 있음)

            for i, line in enumerate(lines, 1):
                stripped = line.strip()

                # 최근 라인 버퍼 (최대 10줄)
                recent_lines.append((i, line))
                if len(recent_lines) > 10:
                    recent_lines.pop(0)

                # 주석 블록 무시
                if stripped.startswith('/*') or stripped.startswith('*'):
                    continue

                # 함수 시작: { 발견
                if not in_function and '{' in line:
                    # 최근 라인들에서 함수 이름 찾기
                    # 패턴: function_name(params) 또는 function_name (params)
                    # 멀티라인 함수 선언 지원: closing ) 없어도 매치
                    for j in range(len(recent_lines) - 1, -1, -1):
                        line_num, prev_line = recent_lines[j]
                        # 함수 이름 패턴 찾기
                        # 패턴 1: 완전한 함수 선언 function_name(...)
                        func_match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\([^)]*\)', prev_line)
                        # 패턴 2: 멀티라인 함수 선언 function_name(..., (closing paren이 다음 줄)
                        if not func_match:
                            func_match = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', prev_line)

                        if func_match:
                            func_name = func_match.group(1)
                            # 키워드 제외 (if, while, for, switch 등)
                            if func_name not in ('if', 'while', 'for', 'switch', 'catch', 'sizeof', 'typeof'):
                                current_func = func_name
                                func_start = line_num
                                brace_depth = line.count('{') - line.count('}')
                                in_function = True
                                break

                elif in_function:
                    brace_depth += line.count('{') - line.count('}')
                    if brace_depth == 0:
                        # 함수 끝
                        if current_func:
                            function_ranges[current_func] = (func_start, i)
                        current_func = None
                        in_function = False

        except Exception as e:
            pass

        return function_ranges


class GcovParser:
    """gcov 출력 파일(.gcov) 파싱"""

    @staticmethod
    def parse_gcov_line(line: str) -> Optional[Tuple[int, int]]:
        """
        gcov 라인 파싱: "실행횟수:라인번호:소스코드"
        Returns: (line_number, execution_count) 또는 None

        형식 예시:
            5:  123:    printf("hello");
        #####:  124:    never_executed();
            -:  125:    // comment
        """
        # gcov 형식: "     count:  line: source"
        match = re.match(r'\s*([^:]+):\s*(\d+):', line)
        if match:
            count_str = match.group(1).strip()
            line_num = int(match.group(2))

            # '-'는 실행 불가능 라인, '#####'는 실행 안된 라인
            if count_str == '-':
                return None  # 실행 불가능 라인은 무시
            elif count_str.startswith('#'):
                return (line_num, 0)  # 실행 안된 라인
            else:
                try:
                    # '*' 제거 (예외 처리 블록 표시)
                    count_str_clean = count_str.rstrip('*')
                    count = int(count_str_clean)
                    return (line_num, count)
                except ValueError:
                    return None
        return None

    @staticmethod
    def run_gcov_for_file(source_file: Path, workspace_dir: Path) -> Optional[Tuple[Dict[int, int], Dict[str, Dict]]]:
        """
        특정 소스 파일에 대해 gcov 실행하고 커버리지 데이터 추출
        Returns: (line_coverage, function_coverage)
            line_coverage: {line_number: execution_count}
            function_coverage: {function_name: {'lines_executed': int, 'lines_total': int}}
        """
        # .h 파일은 스킵 (헤더 파일은 컴파일 안됨)
        if source_file.suffix == '.h':
            return None

        # .gcno 파일 찾기 (소스 파일과 같은 디렉토리)
        gcno_file = source_file.with_suffix('.gcno')

        if not gcno_file.exists():
            # 다른 위치에서 검색 (예: _srv, _shlib 버전)
            source_dir = source_file.parent
            source_base = source_file.stem  # 확장자 제거

            gcno_candidates = list(source_dir.glob(f"{source_base}*.gcno"))
            if not gcno_candidates:
                return None

            # 가장 최근 파일 사용
            gcno_file = max(gcno_candidates, key=lambda p: p.stat().st_mtime)

        gcno_dir = gcno_file.parent
        source_dir = source_file.parent

        # 1. 라인별 커버리지: gcov 기본 실행
        result = subprocess.run(
            ["gcov", "-o", ".", source_file.name],
            cwd=source_dir,
            capture_output=True
        )

        if result.returncode != 0:
            return None

        # .gcov 파일 파싱
        gcov_file = source_dir / f"{source_file.name}.gcov"
        if not gcov_file.exists():
            return None

        line_coverage = {}
        try:
            with open(gcov_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    parsed = GcovParser.parse_gcov_line(line)
                    if parsed:
                        line_num, count = parsed
                        line_coverage[line_num] = count
        finally:
            if gcov_file.exists():
                gcov_file.unlink()

        # 2. 함수별 커버리지: gcov -f 실행
        result_func = subprocess.run(
            ["gcov", "-f", "-o", ".", source_file.name],
            cwd=source_dir,
            capture_output=True
        )

        function_coverage = {}
        if result_func.returncode == 0:
            stdout = result_func.stdout.decode('utf-8', errors='ignore')
            current_func = None

            for line in stdout.split('\n'):
                # Function 'function_name'
                func_match = re.match(r"Function '(.+)'", line)
                if func_match:
                    current_func = func_match.group(1)
                    function_coverage[current_func] = {'lines_executed': 0, 'lines_total': 0}

                # Lines executed:75.00% of 20
                elif current_func and 'Lines executed:' in line:
                    lines_match = re.search(r'Lines executed:[\d.]+% of (\d+)', line)
                    if lines_match:
                        total_lines = int(lines_match.group(1))
                        function_coverage[current_func]['lines_total'] = total_lines

                        # 실행된 라인 수 계산
                        percent_match = re.search(r'Lines executed:([\d.]+)%', line)
                        if percent_match:
                            percent = float(percent_match.group(1))
                            executed = int(total_lines * percent / 100)
                            function_coverage[current_func]['lines_executed'] = executed

        # .gcov 파일 정리
        for f in source_dir.glob("*.gcov"):
            f.unlink()

        return (line_coverage, function_coverage)


class CoverageAnalyzer:
    def __init__(self, output_dir: Optional[str] = None, workspace_dir: str = "."):
        self.workspace_dir = Path(workspace_dir).resolve()

        # 출력 디렉토리 설정
        if output_dir:
            self.output_base = Path(output_dir).resolve()
        else:
            self.output_base = self.workspace_dir / "coverage"

        self.output_base.mkdir(parents=True, exist_ok=True)

        self.html_dir = self.output_base / "html"
        self.untested_file = self.output_base / "untested.md"

    def collect_coverage_for_files(self, files: List[Path], changed_lines: Dict[str, Set[int]]) -> Dict:
        """파일들에 대해 gcov 실행하고 커버리지 수집"""
        print("📊 Collecting coverage data with gcov...")

        coverage_data = {}
        all_lines_mode = len(changed_lines) == 0  # --all 모드

        for i, source_file in enumerate(files, 1):
            file_path = str(source_file)
            print(f"  [{i}/{len(files)}] Processing {source_file.name}...", end='\r')

            # gcov 실행
            result = GcovParser.run_gcov_for_file(source_file, self.workspace_dir)
            if not result:
                print(f"  ⚠️  gcov failed for {source_file.name}", file=sys.stderr)
                continue

            line_coverage, function_coverage = result

            # 변경된 라인 번호들 (git diff에서 추출)
            changed_line_set = changed_lines.get(file_path, set()) if not all_lines_mode else set(line_coverage.keys())

            # 변경된 라인만 필터링 (또는 전체 모드)
            filtered_lines = {}
            for line_num, count in line_coverage.items():
                if all_lines_mode or line_num in changed_line_set:
                    filtered_lines[line_num] = count

            # 변경된 라인이 있거나 gcov 데이터가 있으면 처리
            if changed_line_set or filtered_lines:
                # 함수 범위 파싱하여 변경된 라인이 포함된 함수만 필터링
                function_ranges = SourceParser.find_function_ranges(source_file)
                filtered_functions = {}

                for func_name, func_data in function_coverage.items():
                    # 함수 범위 찾기
                    if func_name in function_ranges:
                        start_line, end_line = function_ranges[func_name]
                        # 변경된 라인이 함수 범위와 겹치는지 확인
                        if any(start_line <= line <= end_line for line in changed_line_set):
                            filtered_functions[func_name] = func_data
                    else:
                        # 함수 범위를 못 찾았으면 포함 (보수적 접근)
                        if all_lines_mode:
                            filtered_functions[func_name] = func_data

                coverage_data[file_path] = {
                    'lines': filtered_lines,  # gcov 데이터 (실행 가능한 라인만)
                    'changed_lines': changed_line_set,  # git diff 데이터 (모든 수정된 라인)
                    'total_lines': len(filtered_lines),
                    'covered_lines': sum(1 for c in filtered_lines.values() if c > 0),
                    'functions': filtered_functions,  # 필터링된 함수 정보
                    'function_ranges': function_ranges  # 함수 범위 정보
                }
            else:
                # 디버그: 왜 제외되었는지
                print(f"  ⚠️  Skipped {source_file.name}: changed_lines={len(changed_line_set)}, filtered_lines={len(filtered_lines)}", file=sys.stderr)

        print(f"\n✅ Coverage data collected for {len(coverage_data)} files")
        return coverage_data

    def generate_html_report(self, coverage_data: Dict) -> bool:
        """HTML 리포트 생성 (메인 페이지 + 파일별 상세 페이지)"""
        print(f"📝 Generating HTML report to {self.html_dir}...")

        self.html_dir.mkdir(parents=True, exist_ok=True)

        # 1. 메인 인덱스 페이지 생성
        self._generate_index_page(coverage_data)

        # 2. 각 파일별 상세 페이지 생성
        for file_path, data in coverage_data.items():
            self._generate_file_page(file_path, data)

        index_file = self.output_base / "index.html"
        print(f"✅ HTML report generated: {index_file}")
        return True

    def _get_blame_info(self, source_file: Path) -> Dict[int, Tuple[str, str]]:
        """
        git blame으로 각 라인의 커밋 정보 수집
        Returns: {line_number: (short_hash, commit_message)}
        """
        blame_info = {}

        try:
            result = subprocess.run(
                ["git", "blame", "-s", "--", str(source_file)],
                cwd=self.workspace_dir,
                capture_output=True,
                timeout=10
            )

            if result.returncode == 0:
                output = result.stdout.decode('utf-8', errors='ignore')
                for line in output.split('\n'):
                    if not line:
                        continue
                    # 형식: hash [filepath] line_num) code
                    # 파일명은 파일 이동 이력이 있을 때만 표시됨
                    # 예1 (파일명 있음): c2e9b2f28818 contrib/pg_upgrade/file.c   1) /*
                    # 예2 (파일명 없음): ^d31084e9d11   1) /*
                    match = re.match(r'^[\^]?([0-9a-f]{7,})\s+(?:\S+\s+)?(\d+)\)', line)
                    if match:
                        commit_hash = match.group(1)[:7]
                        line_num = int(match.group(2))

                        # 커밋 메시지 가져오기 (캐시 사용)
                        if commit_hash not in getattr(self, '_commit_cache', {}):
                            if not hasattr(self, '_commit_cache'):
                                self._commit_cache = {}

                            msg_result = subprocess.run(
                                ["git", "log", "-1", "--format=%s", commit_hash],
                                cwd=self.workspace_dir,
                                capture_output=True,
                                timeout=5
                            )

                            if msg_result.returncode == 0:
                                msg = msg_result.stdout.decode('utf-8', errors='ignore').strip()
                                self._commit_cache[commit_hash] = msg
                            else:
                                self._commit_cache[commit_hash] = ""

                        blame_info[line_num] = (commit_hash, self._commit_cache.get(commit_hash, ""))

        except Exception as e:
            # blame 실패 시 빈 딕셔너리 반환
            pass

        return blame_info

    def _generate_index_page(self, coverage_data: Dict):
        """메인 인덱스 페이지 생성"""
        index_file = self.output_base / "index.html"

        # 통계 계산
        total_files = len(coverage_data)
        total_lines = sum(d['total_lines'] for d in coverage_data.values())
        covered_lines = sum(d['covered_lines'] for d in coverage_data.values())
        coverage_percent = (covered_lines / total_lines * 100) if total_lines > 0 else 0

        # 함수 통계
        total_functions = 0
        covered_functions = 0
        for data in coverage_data.values():
            if 'functions' in data:
                for func_data in data['functions'].values():
                    total_functions += 1
                    if func_data['lines_executed'] > 0:
                        covered_functions += 1

        func_coverage_pct = (covered_functions/total_functions*100 if total_functions > 0 else 0)

        with open(index_file, 'w') as f:
            f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Coverage Report - Overview</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 0; background: #f5f7fa; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color: white; padding: 30px; position: sticky; top: 0; z-index: 100;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        h1 {{ margin: 0; font-size: 2em; }}
        .subtitle {{ opacity: 0.9; margin-top: 10px; }}
        .content-wrapper {{ padding: 20px; }}
        .summary-cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
                        gap: 20px; margin-bottom: 30px; position: sticky; top: 110px; z-index: 50;
                        background: #f5f7fa; padding-top: 20px; margin-top: -20px; }}
        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card-title {{ color: #666; font-size: 0.9em; margin-bottom: 10px; }}
        .card-value {{ font-size: 2em; font-weight: bold; color: #333; }}
        .card-detail {{ color: #999; font-size: 0.9em; margin-top: 5px; }}
        .file-table {{ background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f8f9fa; padding: 15px; text-align: left; font-weight: 600; color: #495057;
             border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 12px 15px; border-bottom: 1px solid #f1f3f5; }}
        tr:hover {{ background: #f8f9fa; }}
        .file-name {{ font-family: 'Courier New', monospace; color: #495057; }}
        .file-link {{ text-decoration: none; color: #667eea; font-weight: 500; }}
        .file-link:hover {{ text-decoration: underline; }}
        .coverage-bar {{ width: 200px; height: 20px; background: #e9ecef; border-radius: 10px; overflow: hidden; }}
        .coverage-fill {{ height: 100%; transition: width 0.3s; }}
        .coverage-high {{ background: linear-gradient(90deg, #51cf66, #37b24d); }}
        .coverage-medium {{ background: linear-gradient(90deg, #ffd43b, #fab005); }}
        .coverage-low {{ background: linear-gradient(90deg, #ff8787, #fa5252); }}
        .coverage-text {{ font-weight: 600; margin-left: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📊 Coverage Report</h1>
        <div class="subtitle">수정된 라인만 분석 | Modified Lines Only</div>
    </div>

    <div class="content-wrapper">
    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Files</div>
            <div class="card-value">{total_files}</div>
            <div class="card-detail">analyzed</div>
        </div>
        <div class="card">
            <div class="card-title">Functions</div>
            <div class="card-value">{covered_functions}/{total_functions}</div>
            <div class="card-detail">{func_coverage_pct:.1f}% covered</div>
        </div>
        <div class="card">
            <div class="card-title">Lines</div>
            <div class="card-value">{covered_lines}/{total_lines}</div>
            <div class="card-detail">{coverage_percent:.1f}% covered</div>
        </div>
        <div class="card nav-help-card">
            <div class="card-title">키보드 네비게이션</div>
            <div style="font-size: 0.85em; line-height: 1.8;">
                <kbd>↑</kbd> <kbd>W</kbd> 이전 파일<br>
                <kbd>↓</kbd> <kbd>S</kbd> 다음 파일<br>
                <kbd>Enter</kbd> <kbd>Tab</kbd> 파일 열기
            </div>
        </div>
    </div>

    <div class="file-table">
        <table>
            <thead>
                <tr>
                    <th>File</th>
                    <th>Coverage</th>
                    <th style="text-align: right;">Lines</th>
                </tr>
            </thead>
            <tbody>
""")

            # 파일 목록 (커버리지 낮은 순으로 정렬)
            # 정렬: 1) 커버리지 낮은 순, 2) 커버리지 같으면 수정 라인 수 많은 순
            sorted_files = sorted(coverage_data.items(),
                                key=lambda x: (
                                    x[1]['covered_lines'] / x[1]['total_lines'] if x[1]['total_lines'] > 0 else 0,
                                    -x[1]['total_lines']  # 음수로 역순 정렬
                                ))

            for file_path, data in sorted_files:
                try:
                    rel_path = Path(file_path).relative_to(self.workspace_dir)
                except ValueError:
                    rel_path = Path(file_path)

                file_coverage = (data['covered_lines'] / data['total_lines'] * 100) if data['total_lines'] > 0 else 0

                # 커버리지 등급
                if file_coverage >= 80:
                    coverage_class = "coverage-high"
                elif file_coverage >= 50:
                    coverage_class = "coverage-medium"
                else:
                    coverage_class = "coverage-low"

                # 디렉토리 구조 유지: src/backend/storage/smgr/md.c → html/src/backend/storage/smgr/md.c.html
                html_filename = str(rel_path) + ".html"

                f.write(f'                <tr>\n')
                f.write(f'                    <td class="file-name"><a class="file-link" href="html/{html_filename}">{rel_path}</a></td>\n')
                f.write(f'                    <td>\n')
                f.write(f'                        <div style="display: flex; align-items: center;">\n')
                f.write(f'                            <div class="coverage-bar">\n')
                f.write(f'                                <div class="coverage-fill {coverage_class}" style="width: {file_coverage}%"></div>\n')
                f.write(f'                            </div>\n')
                f.write(f'                            <span class="coverage-text">{file_coverage:.1f}%</span>\n')
                f.write(f'                        </div>\n')
                f.write(f'                    </td>\n')
                f.write(f'                    <td style="text-align: right;">{data["covered_lines"]}/{data["total_lines"]}</td>\n')
                f.write(f'                </tr>\n')

            f.write("""            </tbody>
        </table>
    </div>
    </div>

    <style>
        tr.selected-file {
            background: #e7f5ff !important;
            outline: 2px solid #667eea;
        }
        .nav-help-card kbd {
            background: #f1f3f5;
            border: 1px solid #dee2e6;
            border-radius: 3px;
            padding: 2px 6px;
            font-family: monospace;
            font-size: 0.9em;
            margin: 0 2px;
        }
    </style>

    <script>
    (function() {
        const fileRows = Array.from(document.querySelectorAll('.file-table tbody tr'));
        let currentIndex = -1;

        // URL hash에서 선택된 파일 확인 (#file=src/bin/pg_waldump/pg_waldump.c)
        const urlHash = window.location.hash;
        if (urlHash.startsWith('#file=')) {
            const fileName = decodeURIComponent(urlHash.substring(6));
            // 해당 파일 찾기
            currentIndex = fileRows.findIndex(row => {
                const link = row.querySelector('.file-link');
                return link && link.textContent === fileName;
            });
            if (currentIndex >= 0) {
                selectRow(currentIndex);
            }
        }

        function selectRow(index) {
            if (index < 0 || index >= fileRows.length) return;

            // 이전 선택 제거
            document.querySelectorAll('.selected-file').forEach(el => el.classList.remove('selected-file'));

            // 새 선택
            fileRows[index].classList.add('selected-file');
            fileRows[index].scrollIntoView({ behavior: 'smooth', block: 'center' });
            currentIndex = index;
        }

        function navigateNext() {
            if (fileRows.length === 0) return;
            const nextIndex = (currentIndex + 1) % fileRows.length;
            selectRow(nextIndex);
        }

        function navigatePrev() {
            if (fileRows.length === 0) return;
            const prevIndex = currentIndex <= 0 ? fileRows.length - 1 : currentIndex - 1;
            selectRow(prevIndex);
        }

        function openSelected() {
            if (currentIndex >= 0 && currentIndex < fileRows.length) {
                const link = fileRows[currentIndex].querySelector('.file-link');
                if (link) {
                    // 현재 선택된 파일명을 URL hash에 저장
                    const fileName = link.textContent;
                    window.location.href = link.href + '#back=' + encodeURIComponent(fileName);
                }
            }
        }

        // 키보드 이벤트
        document.addEventListener('keydown', function(e) {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            // 한글 키보드 지원을 위해 e.code 사용 (물리적 키 위치)
            switch(e.code) {
                case 'ArrowDown':
                case 'ArrowRight':
                case 'KeyS':
                case 'KeyD':
                    e.preventDefault();
                    navigateNext();
                    break;

                case 'ArrowUp':
                case 'ArrowLeft':
                case 'KeyW':
                case 'KeyA':
                    e.preventDefault();
                    navigatePrev();
                    break;

                case 'Enter':
                case 'Tab':
                    e.preventDefault();
                    openSelected();
                    break;
            }
        });

        // 첫 번째 파일 자동 선택 (hash가 없는 경우)
        if (currentIndex < 0 && fileRows.length > 0) {
            selectRow(0);
        }
    })();
    </script>
</body>
</html>
""")

    def _generate_file_page(self, file_path: str, data: Dict):
        """파일별 상세 페이지 생성"""
        try:
            rel_path = Path(file_path).relative_to(self.workspace_dir)
        except ValueError:
            rel_path = Path(file_path)

        # 디렉토리 구조 유지: src/backend/storage/smgr/md.c → html/src/backend/storage/smgr/md.c.html
        html_filename = self.html_dir / (str(rel_path) + ".html")
        html_filename.parent.mkdir(parents=True, exist_ok=True)

        # index.html로 돌아가는 상대 경로 계산
        # html/src/backend/storage/smgr/md.c.html → ../../../index.html
        depth = len(rel_path.parts)  # src, backend, storage, smgr, md.c = 5개
        back_to_index = "../" * depth + "index.html"

        file_coverage = (data['covered_lines'] / data['total_lines'] * 100) if data['total_lines'] > 0 else 0

        # 함수 범위 파싱
        source_file = Path(file_path)
        function_ranges = data.get('function_ranges', SourceParser.find_function_ranges(source_file))

        # git blame 정보 수집
        blame_info = self._get_blame_info(source_file)

        with open(html_filename, 'w') as f:
            f.write(f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{rel_path} - Coverage Report</title>
    <style>
        body {{ font-family: 'Segoe UI', Arial, sans-serif; margin: 0; padding: 0; background: #f5f7fa; }}
        .header {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                  color: white; padding: 30px; position: sticky; top: 0; z-index: 100;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.1); }}
        .back-link {{ color: white; text-decoration: none; font-size: 0.9em; opacity: 0.9; }}
        .back-link:hover {{ text-decoration: underline; opacity: 1; }}
        h1 {{ margin: 10px 0 0 0; color: white; font-size: 1.8em; }}
        .file-stats {{ margin-top: 10px; color: white; opacity: 0.9; }}
        .content-wrapper {{ padding: 20px; }}
        .summary-cards {{ display: grid; grid-template-columns: 1fr 1fr 1fr 1fr;
                        gap: 20px; margin-bottom: 30px; position: sticky; top: 138px; z-index: 50;
                        background: #f5f7fa; padding-top: 20px; margin-top: -20px; }}
        .card {{ background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .card-title {{ color: #666; font-size: 0.9em; margin-bottom: 10px; }}
        .card-value {{ font-size: 2em; font-weight: bold; color: #333; }}
        .card-detail {{ color: #999; font-size: 0.9em; margin-top: 5px; }}
        .nav-help-grid {{ font-size: 0.75em; line-height: 1.4; display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 8px; }}
        .nav-help-col {{ display: flex; flex-direction: column; gap: 2px; }}
        .function-section {{ background: white; padding: 20px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .function-header {{ font-size: 1.2em; font-weight: 600; color: #495057; margin-bottom: 10px; font-family: 'Courier New', monospace; }}
        .function-stats {{ color: #666; margin-bottom: 15px; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th {{ background: #f8f9fa; padding: 10px; text-align: left; font-weight: 600; color: #495057; border-bottom: 2px solid #dee2e6; }}
        td {{ padding: 8px 10px; border-bottom: 1px solid #f1f3f5; }}
        .line-num {{ width: 80px; text-align: right; color: #868e96; font-family: 'Courier New', monospace; font-size: 0.9em; }}
        .hit-count {{ width: 80px; text-align: right; font-family: 'Courier New', monospace; font-weight: 600; }}
        .source-code {{ font-family: 'Courier New', monospace; font-size: 0.9em; white-space: pre; padding-left: 10px; tab-size: 4; -moz-tab-size: 4; }}
        .blame-info {{ width: 600px; font-size: 0.85em; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
        .blame-hash {{ font-family: 'Courier New', monospace; color: #667eea; font-weight: 600; }}
        .blame-msg {{ color: #868e96; margin-left: 8px; }}
        .modified-covered {{ background: #d3f9d8; }}
        .modified-uncovered {{ background: #ffe3e3; }}
        .modified-noexec {{ background: #e7f5ff; }}
        .unmodified {{ background: #f8f9fa; color: #868e96; }}
        .hit-count.hit {{ color: #37b24d; }}
        .hit-count.miss {{ color: #f03e3e; }}
        .hit-count.na {{ color: #adb5bd; }}
        .current-line.modified-covered {{ background: #51cf66; }}
        .current-line.modified-uncovered {{ background: #ff6b6b; }}
        kbd {{ background: #f1f3f5; padding: 2px 6px; border-radius: 3px; font-family: monospace; font-size: 0.9em; border: 1px solid #dee2e6; margin: 0 2px; }}
    </style>
</head>
<body>
    <div class="header">
        <a href="{back_to_index}" class="back-link">← Back to Overview</a>
        <h1>{rel_path}</h1>
        <div class="file-stats">
            <strong>Coverage:</strong> {data['covered_lines']}/{data['total_lines']} lines ({file_coverage:.1f}%)
        </div>
    </div>

    <div class="content-wrapper">

    <div class="summary-cards">
        <div class="card">
            <div class="card-title">Total Lines</div>
            <div class="card-value">{data['total_lines']}</div>
            <div class="card-detail">modified</div>
        </div>
        <div class="card">
            <div class="card-title">Covered</div>
            <div class="card-value">{data['covered_lines']}</div>
            <div class="card-detail">{file_coverage:.1f}%</div>
        </div>
        <div class="card">
            <div class="card-title">Uncovered</div>
            <div class="card-value">{data['total_lines'] - data['covered_lines']}</div>
            <div class="card-detail">{100 - file_coverage:.1f}%</div>
        </div>
        <div class="card">
            <div class="card-title">키보드 네비게이션</div>
            <div class="nav-help-grid">
                <div class="nav-help-col">
                    <div><kbd>↑</kbd><kbd>W</kbd> 이전 미실행</div>
                    <div><kbd>↓</kbd><kbd>S</kbd> 다음 미실행</div>
                    <div><kbd>←</kbd><kbd>A</kbd> 이전 수정</div>
                    <div><kbd>→</kbd><kbd>D</kbd> 다음 수정</div>
                </div>
                <div class="nav-help-col">
                    <div><kbd>PgUp</kbd><kbd>Q</kbd> 이전함수</div>
                    <div><kbd>PgDn</kbd><kbd>E</kbd> 다음함수</div>
                    <div><kbd>Home</kbd><kbd>C-A</kbd> 시작</div>
                    <div><kbd>End</kbd><kbd>C-E</kbd> 끝</div>
                </div>
                <div class="nav-help-col">
                    <div><kbd>ESC</kbd><kbd>BS</kbd> 목록</div>
                </div>
            </div>
        </div>
    </div>
""")

            # 소스 파일 읽기
            try:
                with open(source_file, 'r', encoding='utf-8', errors='ignore') as src:
                    source_lines = src.readlines()
            except Exception as e:
                source_lines = []

            # git diff로 추출한 모든 수정된 라인 (주석, 실행 불가능 라인 포함)
            all_changed_lines = data.get('changed_lines', set())

            # 함수별로 수정된 라인 그룹핑
            functions_with_changes = {}
            unassigned_lines = []

            for line_num in sorted(all_changed_lines):
                found = False
                for func_name, (start, end) in function_ranges.items():
                    if start <= line_num <= end:
                        if func_name not in functions_with_changes:
                            functions_with_changes[func_name] = (start, end)
                        found = True
                        break
                if not found:
                    unassigned_lines.append(line_num)

            # 함수별 섹션 (수정된 라인이 있는 함수만, 라인 번호 순서로 정렬)
            for func_name in sorted(functions_with_changes.keys(), key=lambda f: functions_with_changes[f][0]):
                start_line, end_line = functions_with_changes[func_name]

                # 함수 내 수정된 라인만으로 커버리지 계산 (gcov 데이터가 있는 것만)
                modified_lines_in_func = [ln for ln in all_changed_lines if start_line <= ln <= end_line and ln in data['lines']]
                func_total = len(modified_lines_in_func)
                func_covered = sum(1 for ln in modified_lines_in_func if data['lines'][ln] > 0)
                func_coverage = (func_covered / func_total * 100) if func_total > 0 else 0

                f.write(f'    <div class="function-section">\n')
                f.write(f'        <div class="function-header">{func_name}() <span style="color: #adb5bd; font-size: 0.8em;">lines {start_line}-{end_line}</span></div>\n')
                f.write(f'        <div class="function-stats">Modified Lines Coverage: {func_covered}/{func_total} lines ({func_coverage:.1f}%)</div>\n')
                f.write(f'        <table>\n')
                f.write(f'            <tr><th>Line</th><th>Hits</th><th>Source</th><th>Commit</th></tr>\n')

                # 함수 범위 내의 모든 라인 표시
                for line_num in range(start_line, end_line + 1):
                    # 소스 코드 가져오기
                    if source_lines and 0 < line_num <= len(source_lines):
                        source_code = source_lines[line_num - 1].rstrip('\n')
                    else:
                        source_code = ""

                    # 수정된 라인인지 확인 (git diff 기준)
                    is_modified = line_num in all_changed_lines

                    if is_modified:
                        # gcov 데이터가 있는지 확인
                        hit_count = data['lines'].get(line_num, None)
                        if hit_count is not None:
                            # 실행 가능한 라인
                            row_class = "modified-covered" if hit_count > 0 else "modified-uncovered"
                            hit_class = "hit" if hit_count > 0 else "miss"
                            hit_display = str(hit_count)
                        else:
                            # 실행 불가능한 라인 (주석, 선언 등)
                            row_class = "modified-noexec"
                            hit_class = "na"
                            hit_display = "-"
                    else:
                        # 수정되지 않은 라인
                        row_class = "unmodified"
                        hit_class = "na"
                        hit_display = "-"

                    # HTML 이스케이프
                    source_code = source_code.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

                    # Blame 정보 (수정된 라인에만 표시)
                    if is_modified and line_num in blame_info:
                        commit_hash, commit_msg = blame_info[line_num]
                        commit_msg_escaped = commit_msg.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        blame_html = f'<span class="blame-hash">{commit_hash}</span><span class="blame-msg">{commit_msg_escaped}</span>'
                    else:
                        blame_html = '<span class="blame-msg">-</span>'

                    f.write(f'            <tr class="{row_class}">\n')
                    f.write(f'                <td class="line-num">{line_num}</td>\n')
                    f.write(f'                <td class="hit-count {hit_class}">{hit_display}</td>\n')
                    f.write(f'                <td class="source-code">{source_code}</td>\n')
                    f.write(f'                <td class="blame-info">{blame_html}</td>\n')
                    f.write(f'            </tr>\n')

                f.write(f'        </table>\n')
                f.write(f'    </div>\n')

            f.write("""    </div>

    <script>
    (function() {
        // 수정된 라인 찾기 (파란색 제외 - 녹색과 빨강만)
        const modifiedRows = Array.from(document.querySelectorAll('tr.modified-covered, tr.modified-uncovered'));
        // 미실행 라인만 찾기 (빨강)
        const untestedRows = Array.from(document.querySelectorAll('tr.modified-uncovered'));
        // 모든 함수 섹션 찾기
        const functionSections = Array.from(document.querySelectorAll('.function-section'));

        // 연속된 라인들을 그룹으로 묶기 (같은 색상끼리만)
        function groupConsecutiveRows(rows) {
            if (rows.length === 0) return [];

            const groups = [];
            let currentGroup = [rows[0]];

            for (let i = 1; i < rows.length; i++) {
                const prevRow = currentGroup[currentGroup.length - 1];
                const currRow = rows[i];

                const prevLineNum = parseInt(prevRow.querySelector('.line-num').textContent);
                const currLineNum = parseInt(currRow.querySelector('.line-num').textContent);

                // 같은 색상인지 확인 (녹색: modified-covered, 빨강: modified-uncovered)
                const prevIsCovered = prevRow.classList.contains('modified-covered');
                const currIsCovered = currRow.classList.contains('modified-covered');
                const sameColor = prevIsCovered === currIsCovered;

                // 라인 번호가 연속되고 같은 색상이면 같은 그룹
                if (currLineNum === prevLineNum + 1 && sameColor) {
                    currentGroup.push(currRow);
                } else {
                    // 새 그룹 시작
                    groups.push(currentGroup);
                    currentGroup = [currRow];
                }
            }
            groups.push(currentGroup);
            return groups;
        }

        const modifiedGroups = groupConsecutiveRows(modifiedRows);
        const untestedGroups = groupConsecutiveRows(untestedRows);

        // 현재 선택된 그룹 인덱스 추적
        let currentModifiedIndex = -1;
        let currentUntestedIndex = -1;

        // 현재 선택된 그룹 찾기 (current-line 클래스가 있는 행)
        function findCurrentIndex(groups) {
            for (let i = 0; i < groups.length; i++) {
                for (let row of groups[i]) {
                    if (row.classList.contains('current-line')) {
                        return i;
                    }
                }
            }
            return -1;
        }

        function findClosestFunctionIndex() {
            if (functionSections.length === 0) return -1;

            const viewportCenter = window.scrollY + window.innerHeight / 2;
            let closestIndex = 0;
            let minDistance = Math.abs(functionSections[0].getBoundingClientRect().top + window.scrollY - viewportCenter);

            for (let i = 1; i < functionSections.length; i++) {
                const distance = Math.abs(functionSections[i].getBoundingClientRect().top + window.scrollY - viewportCenter);
                if (distance < minDistance) {
                    minDistance = distance;
                    closestIndex = i;
                }
            }

            return closestIndex;
        }

        function scrollToGroup(group) {
            if (!group || group.length === 0) return;

            // 이전 하이라이트 제거
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));

            // 그룹의 모든 라인에 하이라이트 추가
            group.forEach(row => row.classList.add('current-line'));

            // 그룹의 첫 번째 라인으로 스크롤
            group[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        let lastFunctionScrolled = null;

        function scrollToFunction(section) {
            if (!section) return;

            // 하이라이트 제거
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));

            // sticky 헤더 높이 계산
            const header = document.querySelector('.header');
            const summaryCards = document.querySelector('.summary-cards');
            const headerHeight = header ? header.offsetHeight : 0;
            const cardsHeight = summaryCards ? summaryCards.offsetHeight : 0;
            const totalStickyHeight = headerHeight + cardsHeight + 20; // 여유 20px

            // 함수 섹션 위치 계산 및 스크롤
            const sectionTop = section.getBoundingClientRect().top + window.scrollY;
            window.scrollTo({
                top: sectionTop - totalStickyHeight,
                behavior: 'smooth'
            });

            // 마지막으로 이동한 함수 기록
            lastFunctionScrolled = section;
        }

        // 함수 내의 수정 라인 그룹 찾기
        function findGroupsInFunction(section, groups) {
            if (!section) return [];

            const sectionRows = Array.from(section.querySelectorAll('tr.modified-covered, tr.modified-uncovered'));
            return groups.filter(group => sectionRows.includes(group[0]));
        }

        function navigateNext() {
            if (modifiedGroups.length === 0) return;

            // 함수 이동 직후라면, 해당 함수의 첫 번째 수정 라인으로
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, modifiedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentModifiedIndex = modifiedGroups.indexOf(groupsInFunction[0]);
                    scrollToGroup(groupsInFunction[0]);
                    return;
                }
            }

            // 현재 인덱스 갱신
            currentModifiedIndex = findCurrentIndex(modifiedGroups);
            if (currentModifiedIndex === -1) {
                currentModifiedIndex = 0;
            } else {
                currentModifiedIndex = (currentModifiedIndex + 1) % modifiedGroups.length;
            }
            scrollToGroup(modifiedGroups[currentModifiedIndex]);
        }

        function navigatePrev() {
            if (modifiedGroups.length === 0) return;

            // 함수 이동 직후라면, 해당 함수의 마지막 수정 라인으로
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, modifiedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentModifiedIndex = modifiedGroups.indexOf(groupsInFunction[groupsInFunction.length - 1]);
                    scrollToGroup(groupsInFunction[groupsInFunction.length - 1]);
                    return;
                }
            }

            // 현재 인덱스 갱신
            currentModifiedIndex = findCurrentIndex(modifiedGroups);
            if (currentModifiedIndex === -1) {
                currentModifiedIndex = modifiedGroups.length - 1;
            } else {
                currentModifiedIndex = currentModifiedIndex <= 0 ? modifiedGroups.length - 1 : currentModifiedIndex - 1;
            }
            scrollToGroup(modifiedGroups[currentModifiedIndex]);
        }

        function navigateUntestedNext() {
            if (untestedGroups.length === 0) return;

            // 함수 이동 직후라면, 해당 함수의 첫 번째 미실행 라인으로
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, untestedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentUntestedIndex = untestedGroups.indexOf(groupsInFunction[0]);
                    scrollToGroup(groupsInFunction[0]);
                    return;
                }
            }

            // 현재 인덱스 갱신
            currentUntestedIndex = findCurrentIndex(untestedGroups);
            if (currentUntestedIndex === -1) {
                currentUntestedIndex = 0;
            } else {
                currentUntestedIndex = (currentUntestedIndex + 1) % untestedGroups.length;
            }
            scrollToGroup(untestedGroups[currentUntestedIndex]);
        }

        function navigateUntestedPrev() {
            if (untestedGroups.length === 0) return;

            // 함수 이동 직후라면, 해당 함수의 마지막 미실행 라인으로
            if (lastFunctionScrolled) {
                const groupsInFunction = findGroupsInFunction(lastFunctionScrolled, untestedGroups);
                if (groupsInFunction.length > 0) {
                    lastFunctionScrolled = null;
                    currentUntestedIndex = untestedGroups.indexOf(groupsInFunction[groupsInFunction.length - 1]);
                    scrollToGroup(groupsInFunction[groupsInFunction.length - 1]);
                    return;
                }
            }

            // 현재 인덱스 갱신
            currentUntestedIndex = findCurrentIndex(untestedGroups);
            if (currentUntestedIndex === -1) {
                currentUntestedIndex = untestedGroups.length - 1;
            } else {
                currentUntestedIndex = currentUntestedIndex <= 0 ? untestedGroups.length - 1 : currentUntestedIndex - 1;
            }
            scrollToGroup(untestedGroups[currentUntestedIndex]);
        }

        function navigateFunctionNext() {
            if (functionSections.length === 0) return;

            const currentIndex = findClosestFunctionIndex();
            const nextIndex = currentIndex + 1;
            // 롤오버 방지: 마지막 함수에서 다음 함수로 이동 안 함
            if (nextIndex >= functionSections.length) return;
            scrollToFunction(functionSections[nextIndex]);
        }

        function navigateFunctionPrev() {
            if (functionSections.length === 0) return;

            const currentIndex = findClosestFunctionIndex();
            const prevIndex = currentIndex - 1;
            // 롤오버 방지: 첫 번째 함수에서 이전 함수로 이동 안 함
            if (prevIndex < 0) return;
            scrollToFunction(functionSections[prevIndex]);
        }

        function jumpToTop() {
            // 하이라이트 제거
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));
            // 페이지 맨 위로
            window.scrollTo({ top: 0, behavior: 'smooth' });
            lastFunctionScrolled = null;
        }

        function jumpToBottom() {
            // 하이라이트 제거
            document.querySelectorAll('.current-line').forEach(el => el.classList.remove('current-line'));
            // 페이지 맨 아래로
            window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' });
            lastFunctionScrolled = null;
        }

        function goToIndex() {
            // 현재 파일명을 hash로 전달하여 index.html에서 해당 파일 선택
            const fileName = """ + f'"{rel_path}"' + """;
            window.location.href = """ + f'"{back_to_index}"' + """ + "#file=" + encodeURIComponent(fileName);
        }

        // 키보드 이벤트
        document.addEventListener('keydown', function(e) {
            // 입력 필드에서는 무시
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

            // Ctrl-A (기본 전체 선택 방지) - e.key 사용 (Ctrl 조합은 언어 무관)
            if (e.ctrlKey && e.key === 'a') {
                e.preventDefault();
                jumpToTop();
                return;
            }

            // Ctrl-E - e.key 사용 (Ctrl 조합은 언어 무관)
            if (e.ctrlKey && e.key === 'e') {
                e.preventDefault();
                jumpToBottom();
                return;
            }

            // 한글 키보드 지원을 위해 e.code 사용 (물리적 키 위치)
            switch(e.code) {
                case 'Home':
                    e.preventDefault();
                    jumpToTop();
                    break;

                case 'End':
                    e.preventDefault();
                    jumpToBottom();
                    break;

                case 'Escape':
                case 'Backspace':
                    e.preventDefault();
                    goToIndex();
                    break;

                case 'ArrowLeft':
                case 'KeyA':
                    e.preventDefault();
                    navigatePrev();
                    break;

                case 'ArrowRight':
                case 'KeyD':
                    e.preventDefault();
                    navigateNext();
                    break;

                case 'ArrowUp':
                case 'KeyW':
                    e.preventDefault();
                    navigateUntestedPrev();
                    break;

                case 'ArrowDown':
                case 'KeyS':
                    e.preventDefault();
                    navigateUntestedNext();
                    break;

                case 'KeyQ':
                case 'PageUp':
                    e.preventDefault();
                    navigateFunctionPrev();
                    break;

                case 'KeyE':
                case 'PageDown':
                    e.preventDefault();
                    navigateFunctionNext();
                    break;
            }
        });
    })();
    </script>
</body>
</html>
""")

    def generate_untested_report(self, coverage_data: Dict) -> bool:
        """테스트되지 않은 라인 정보를 Markdown 체크리스트로 생성"""
        print(f"📋 Generating untested lines report to {self.untested_file}...")

        with open(self.untested_file, 'w') as f:
            f.write("# 테스트되지 않은 코드 분석 리포트 (수정된 라인만)\n\n")
            f.write(f"**생성 시간**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write("---\n\n")

            total_files = len(coverage_data)
            untested_files = 0
            total_lines = 0
            untested_lines = 0

            # 파일별 상세 정보
            for file_path, data in sorted(coverage_data.items()):
                try:
                    rel_path = Path(file_path).relative_to(self.workspace_dir)
                except ValueError:
                    rel_path = file_path

                file_total_lines = data['total_lines']
                file_covered_lines = data['covered_lines']
                file_untested_lines = file_total_lines - file_covered_lines

                # 전체 통계 집계 (모든 파일 포함)
                total_lines += file_total_lines
                untested_lines += file_untested_lines

                # 100% 커버된 파일은 상세 출력 생략
                if file_untested_lines == 0:
                    continue

                untested_files += 1

                coverage_percent = (file_covered_lines / file_total_lines * 100) if file_total_lines > 0 else 0

                f.write(f"## 📄 {rel_path}\n\n")
                f.write(f"**파일 커버리지**: {file_covered_lines}/{file_total_lines} lines ({coverage_percent:.1f}%)\n\n")

                # 함수 범위 파싱
                source_file = Path(file_path)
                function_ranges = SourceParser.find_function_ranges(source_file)

                # 테스트되지 않은 라인들
                untested_line_nums = sorted([ln for ln, count in data['lines'].items() if count == 0])

                if not untested_line_nums:
                    continue

                # 함수별로 미테스트 라인 그룹핑
                lines_by_function = defaultdict(list)
                unassigned_lines = []

                for line_num in untested_line_nums:
                    # 어느 함수에 속하는지 찾기
                    found = False
                    for func_name, (start, end) in function_ranges.items():
                        if start <= line_num <= end:
                            lines_by_function[func_name].append(line_num)
                            found = True
                            break
                    if not found:
                        unassigned_lines.append(line_num)

                # 함수별로 출력
                for func_name in sorted(lines_by_function.keys()):
                    func_lines = lines_by_function[func_name]
                    start_line, end_line = function_ranges[func_name]

                    # 함수 내 전체 라인 수 (수정된 라인만)
                    func_total_lines = sum(1 for ln in data['lines'].keys() if start_line <= ln <= end_line)
                    func_covered_lines = sum(1 for ln, count in data['lines'].items()
                                           if start_line <= ln <= end_line and count > 0)
                    func_coverage = (func_covered_lines / func_total_lines * 100) if func_total_lines > 0 else 0

                    f.write(f"### 함수: `{func_name}` (lines {start_line}-{end_line})\n\n")
                    f.write(f"**함수 커버리지**: {func_covered_lines}/{func_total_lines} lines ({func_coverage:.1f}%)\n\n")

                    # 연속된 범위로 그룹화
                    ranges = []
                    range_start = func_lines[0]
                    range_end = func_lines[0]

                    for line_num in func_lines[1:]:
                        if line_num == range_end + 1:
                            range_end = line_num
                        else:
                            if range_start == range_end:
                                ranges.append(f"{range_start}")
                            else:
                                ranges.append(f"{range_start}-{range_end}")
                            range_start = line_num
                            range_end = line_num

                    # 마지막 범위
                    if range_start == range_end:
                        ranges.append(f"{range_start}")
                    else:
                        ranges.append(f"{range_start}-{range_end}")

                    f.write(f"#### 🔴 테스트되지 않은 라인 ({len(ranges)}개 범위)\n\n")
                    for line_range in ranges:
                        f.write(f"- [ ] `{rel_path}:{line_range}`\n")
                    f.write("\n")

                f.write("---\n\n")

            # 함수 통계 계산
            total_functions = 0
            covered_functions = 0

            for file_path, data in coverage_data.items():
                if 'functions' in data:
                    for func_name, func_data in data['functions'].items():
                        total_functions += 1
                        if func_data['lines_executed'] > 0:
                            covered_functions += 1

            # 요약 통계
            f.write("## 📊 요약 통계\n\n")
            f.write(f"- **파일**: {untested_files}/{total_files} 파일에 미테스트 코드 존재\n")

            if total_functions > 0:
                uncovered_functions = total_functions - covered_functions
                func_coverage = covered_functions / total_functions * 100
                f.write(f"- **함수**: {covered_functions}/{total_functions} 함수 커버됨 "
                       f"({func_coverage:.1f}%)\n")
                f.write(f"  - 미테스트: {uncovered_functions} 함수\n")

            if total_lines > 0:
                covered_lines = total_lines - untested_lines
                line_coverage = covered_lines / total_lines * 100
                f.write(f"- **라인**: {covered_lines}/{total_lines} 라인 커버됨 "
                       f"({line_coverage:.1f}%) - **수정된 라인만**\n")
                f.write(f"  - 미테스트: {untested_lines} 라인\n")

            f.write("\n---\n\n")
            f.write("## 💡 사용 가이드\n\n")
            f.write("1. 각 항목 앞의 `- [ ]`를 `- [x]`로 변경하여 작업 완료 표시\n")
            f.write("2. 파일 경로 형식 `path:line` 또는 `path:start-end`로 정확한 위치 확인 가능\n")
            f.write("3. Claude Code에게 이 파일을 제공하여 테스트 케이스 작성 요청\n")
            f.write("   - 예: \"untested.md의 라인들에 대한 화이트박스 테스트를 작성해줘\"\n")
            f.write("4. **주의**: 이 리포트는 수정된 라인만 포함합니다 (전체 파일 커버리지 아님)\n")

        print(f"✅ Untested lines report generated: {self.untested_file}")
        return True

    def run(self, commit_range: str) -> bool:
        """전체 프로세스 실행"""
        print(f"🚀 Starting coverage analysis for: {commit_range or 'all files'}")
        print(f"📁 Output directory: {self.output_base}\n")

        # 1. Git diff로 변경된 라인 추출
        print("🔍 Analyzing git diff for changed lines...")
        changed_lines = GitDiffParser.get_changed_lines(self.workspace_dir, commit_range)

        # 2. 변경된 파일 목록
        if commit_range != "--all":
            if not changed_lines:
                print("❌ No .c or .h files changed in the commit range")
                return False

            files = [Path(f) for f in changed_lines.keys()]
            total_changed_lines = sum(len(lines) for lines in changed_lines.values())
            print(f"📁 Found {len(files)} files with {total_changed_lines} changed lines\n")
        else:
            # 전체 모드: 모든 .c, .h 파일
            print("📁 Scanning all source files...")
            files = list(self.workspace_dir.rglob("*.c")) + list(self.workspace_dir.rglob("*.h"))
            files = [f for f in files if f.exists()]
            print(f"📁 Found {len(files)} source files\n")

        # 3. gcov로 커버리지 수집
        coverage_data = self.collect_coverage_for_files(files, changed_lines)

        if not coverage_data:
            print("❌ No coverage data collected")
            return False

        # 4. HTML 리포트 생성
        if not self.generate_html_report(coverage_data):
            return False

        # 5. 미테스트 라인 리포트 생성
        if not self.generate_untested_report(coverage_data):
            return False

        print("\n" + "=" * 80)
        print("✅ Coverage analysis complete!")
        print("=" * 80)
        print(f"📊 HTML Report  : {self.output_base / 'index.html'}")
        print(f"📋 Untested Code: {self.untested_file}")
        print(f"📁 All outputs  : {self.output_base}")
        print("=" * 80)

        return True


def main():
    parser = argparse.ArgumentParser(
        description='Git 커밋 기반 코드 커버리지 리포트 생성 (수정된 라인만, gcov 사용)',
        epilog='''
Examples:
  ./gencov.py abc123              # abc123 커밋에서 수정된 라인만
  ./gencov.py HEAD~5              # 5개 전 커밋에서 수정된 라인만
  ./gencov.py abc123..def456      # 커밋 범위에서 수정된 라인만
  ./gencov.py -o coverage master  # 출력 디렉토리 지정
  ./gencov.py --all               # 전체 소스 (모든 라인)
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        'commit_range',
        nargs='?',
        help='Git 커밋 또는 범위 (예: HEAD~10, abc123, abc123..def456, --all)'
    )

    parser.add_argument(
        '-o', '--output-dir',
        help='출력 디렉토리 (기본값: coverage)'
    )

    args = parser.parse_args()

    if not args.commit_range:
        parser.print_help()
        sys.exit(1)

    analyzer = CoverageAnalyzer(output_dir=args.output_dir)
    success = analyzer.run(args.commit_range)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

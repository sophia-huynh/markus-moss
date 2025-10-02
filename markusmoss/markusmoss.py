from __future__ import annotations
import collections
import glob
import os
import sys
import csv
import shutil
import subprocess
import zipfile
import mosspy
import markusapi
import requests
import io
import bs4
import re
from typing import Optional, ClassVar, Tuple, Iterable, Dict, Pattern, Iterator

from bs4 import BeautifulSoup


class _HighlightedFile:
    def __init__(self, filename: str, content_path: str, language: str) -> None:
        self.filename = filename
        self.language = language
        self.content_path = content_path
        self._highlighted_lines = []

    def add_highlight(self, start: int, end: int) -> None:
        """Add the lines between (start, end) to be highlighted.
        """
        for i in range(len(self._highlighted_lines)):
            s, e, = self._highlighted_lines[i]

            if end < s:
                # If our pair ends before this one, simply insert.
                self._highlighted_lines.insert(i, [start, end])
                return
            elif end <= e:
                # If the end appears within the pair, modify the start as needed
                self._highlighted_lines[i][0] = min(s, start)
                return
            elif start <= e:
                # If the start appears within the pair, modify the end as needed
                self._highlighted_lines[i][1] = max(e, end)
                return

        # Otherwise we are beyond all pairs and just add to the end
        self._highlighted_lines.append([start, end])

    def _code_block_template(self, highlight: bool = False):
        hl = ' highlight' if highlight else ''
        return f'<pre class="{self.language} numberLines{hl}"' + \
            """firstnumber="{start}">{code}</pre>"""

    def format_block(self, lines: list[str], starting_index: int,
                     highlight: bool = False) -> str:
        """Return a formatted <pre> block with the lines of code in lines.
        """
        return self._code_block_template(highlight).format(start=starting_index,
                                                           code="".join(lines))

    def make_html(self) -> str:
        """Return HTML that corresponds to the contents of this _HighlightedFile.
        """
        with open(self.content_path) as f:
            contents = f.readlines()
        code_blocks = [f"<h1>{self.filename}</h1>"]
        current_line_start = 0
        for (start, end) in self._highlighted_lines:
            # Add everything from current_line_start to start
            lines = contents[current_line_start:start - 1]
            code_blocks.append(self.format_block(lines, current_line_start + 1))

            # Add everything in start, end
            lines = contents[start - 1:end]
            code_blocks.append(self.format_block(lines, start, highlight=True))
            current_line_start = end

        # Add the remaining lines
        lines = contents[current_line_start:]
        if lines:
            code_blocks.append(self.format_block(lines, current_line_start + 1))

        return "\n".join(code_blocks)


class _GroupFiles:
    def __init__(self, name: str, cover: str, members: list | None = None) -> None:
        self.name = name
        if members:
            self.members = ["{} {}".format(member['first_name'], member['last_name'])
                            for member in members]
        else:
            self.members = [name]
        self.cover = cover
        self.files = {}

    def add_file(self, name: str, path: str, override: bool = False) -> None:
        """Add the given file to the group's list of files.

        If the given name was already added, only replace it if override is True.
        """
        if name not in self.files or override:
            self.files[name] = path

    @property
    def group_header(self):
        members = ", ".join(self.members)

        if len(self.members) == 1:
            return members

        return f"{self.name} ({members})"

    def add_to_merger(self, pdf_merger) -> None:
        """Make a single PDF for the group including the cover page and
        all of their files (in alphabetical order).
        """
        from toc_pdf_merge import IncludePDF
        pdf_merger.add_pdf(IncludePDF(self.cover, self.group_header))

        for file in sorted(self.files.keys()):
            pdf_merger.add_pdf(IncludePDF(self.files[file]))


class _MatchDetails:
    """Tracks the details about one part of a match.
    """
    filepath: str
    header: str
    start: int
    end: int
    code: str

    def __init__(self, filepath: str, header: str,
                 start: int, end: int, code: str) -> None:
        self.filepath = filepath
        self.header = header
        self.start = start
        self.end = end
        self.code = code

    def __iter__(self):
        for attr in [self.filepath, self.header, self.start, self.end, self.code]:
            yield attr


class _Case:
    matches: list[tuple[_MatchDetails, _MatchDetails]]
    _markus_moss: MarkusMoss

    def __init__(self, case_file: str, mm: MarkusMoss) -> None:
        """
        Initialize this _Case based on information in case_file.
        """
        with open(case_file) as f:
            html = BeautifulSoup(f.read(), features='html5lib')
        top_table = html.select_one("#top").select_one("table")

        # Keep the headers of the match
        current_headers = ('', '')

        # Build a list of match tuples in the form
        # [((href1, start, end), (href2, start, end))]
        href_line_pairs = []
        for row in top_table.select("tr"):
            headers = row.select("th")
            data = row.select("td")
            if headers:
                current_headers = tuple([headers[i].contents[0] for i in range(0, len(headers), 2)])
            else:
                current_pairs = []
                for i in range(0, len(data), 2):
                    a_href = data[i].select_one("a")
                    href = a_href.get("href").strip("#")
                    start, end = (int(num) for num in a_href.contents[0].split("-"))
                    current_pairs.append((href, start, end))
                href_line_pairs.append(tuple(current_pairs))

        # Maps the match IDs to the code
        href_to_code = {}

        for match_id in ["#match-0", "#match-1"]:
            match = html.select_one(match_id)
            a_hrefs = match.select("a")
            for a_href in a_hrefs:
                match_id = a_href.get("id")
                if not match_id:
                    continue

                # Code is in the font tag following the anchor (and is the last element.)
                content = a_href.next_element.contents[-1]
                href_to_code[match_id] = content

        matches = []
        for pair in href_line_pairs:
            current_pair = []
            for i in range(len(pair)):
                match, original_header = pair[i], current_headers[i]
                filepath = mm.get_path_from_header(original_header)
                header = mm.format_header(original_header)
                href, start, end = match
                code = href_to_code[href].strip("\n")

                current_pair.append(_MatchDetails(filepath, f"{header}", start, end,
                                                  str(code)))
            matches.append(tuple(current_pair))

        self.matches = matches
        self._markus_moss = mm

    def get_name(self) -> str:
        """Return the header/name for this _Case.
        The name for a case is in the form:
        <group name> <file> VS <group name> <file> as defined by the headers,
        and is based on the first match in this case.

        If a name cannot be identified, return an empty string.
        """
        if not self.matches:
            return ''

        m1, m2 = self.matches[0]
        h1 = m1.header
        h2 = m2.header

        return f"{h1} VS {h2}"

    def __len__(self) -> int:
        return len(self.matches)

    def __iter__(self):
        for pair in self.matches:
            yield pair

    def __getitem__(self, item: int):
        return self.matches[item]


class MarkusMoss:
    CODE_BLOCK_LIMIT: ClassVar[int] = 20
    SUBMISSION_FILES_DIR: ClassVar[str] = "submission_files"
    GROUP_MEMBERSHIP_FILE: ClassVar[str] = "group_data.csv"
    PDF_SUBMISSION_FILES_DIR: ClassVar[str] = "pdf_submission_files"
    STARTER_FILES_DIR: ClassVar[str] = "starter_files"
    MOSS_REPORT_DIR: ClassVar[str] = "moss_report"
    MOSS_REPORT_URL: ClassVar[str] = "report_url.txt"
    MOSS_REPORT_DOWNLOAD: ClassVar[str] = "report"
    FINAL_REPORT_DIR: ClassVar[str] = "final_report"
    FINAL_REPORT_CASE_OVERVIEW: ClassVar[str] = "case_overview.csv"
    SELECTED_CASES_DIR: ClassVar[str] = "selected"
    OVERVIEW_INFO: ClassVar[Tuple[str]] = ("case", "groups", "similarity (%)", "matched_lines")
    USER_INFO: ClassVar[Tuple[str]] = ("group_name", "user_name", "first_name", "last_name", "email", "id_number")
    PRINT_PREFIX: ClassVar[str] = "[MARKUSMOSS]"
    ACTIONS: ClassVar[Tuple[str]] = (
        "download_submission_files",
        "download_starter_files",
        "copy_files_to_pdf",
        "run_moss",
        "download_moss_report",
        "write_final_report",
    )
    SELECT_OUTPUT_REPORT: ClassVar[str] = "case_report"

    def __init__(
            self,
            markus_api_key: Optional[str] = None,
            markus_url: Optional[str] = None,
            markus_assignment: Optional[str] = None,
            markus_course: Optional[str] = None,
            moss_userid: Optional[int] = None,
            moss_report_url: Optional[str] = None,
            workdir: Optional[str] = None,
            language: Optional[str] = None,
            groups: Optional[list[str]] = None,
            file_glob: str = "**/*",
            force: bool = False,
            verbose: bool = False,
            selected_groups: Optional[list[str]] = None,
            exclude_matches: Optional[dict[int | str, list[int]]] = None
    ) -> None:
        self.force = force
        self.verbose = verbose
        self.file_glob = file_glob
        self.groups = groups

        if (selected_groups and
                len(selected_groups) > 1 and
                isinstance(selected_groups[0], str)):
            # Ensure that group names are in a list of lists, rather than
            # a list of strings
            selected_groups = [selected_groups]

        self.selected_groups = selected_groups if selected_groups else []
        self.exclude_matches = exclude_matches
        self.__group_data = None
        self.__membership_data = None
        self.__assignment_id = None
        self.__api = None
        self.__moss = None
        self.__report_regex = None
        self.__starter_file_groups = None
        self.__markus_api_key = markus_api_key
        self.__markus_url = markus_url
        self.__markus_assignment = markus_assignment
        self.__markus_course = markus_course
        self.__markus_course_id = None
        self.__moss_userid = moss_userid
        self.__moss_report_url = moss_report_url
        self.__workdir = workdir
        self.__language = language

    def run(self, actions: Optional[Iterable[str]] = None) -> None:
        if actions is None:
            actions = self.ACTIONS

        if self.selected_groups and "select_match" not in actions:
            actions += ["select_match"]

        for action in actions:
            getattr(self, action)()

    def download_submission_files(self) -> None:
        for data in self._group_data:
            clean_filename = self._clean_filename(data["group_name"])
            destination = os.path.join(self.submission_files_dir, clean_filename)
            if os.path.isdir(destination) and not self.force:
                continue
            self._print(f"Downloading submission files for group: {data['group_name']}")
            zip_byte_stream = self.api.get_files_from_repo(self._markus_course_id, self._assignment_id, data["id"],
                                                           collected=True)
            if not isinstance(zip_byte_stream, bytes):
                sys.stderr.write(f"[MARKUSAPI ERROR]{zip_byte_stream}\n")
                sys.stderr.flush()
                continue
            self._unzip_file(zip_byte_stream, destination)

    def copy_files_to_pdf(self) -> None:
        self._copy_files_to_pdf(self.submission_files_dir, self.pdf_submission_files_dir)
        self._copy_files_to_pdf(self.org_starter_files_dir, self.pdf_starter_files_dir)

    def download_starter_files(self) -> None:
        for group_data in self._starter_file_groups:
            destination = os.path.join(self.org_starter_files_dir, str(group_data["id"]))
            if os.path.isdir(destination) and not self.force:
                continue
            self._print(f"Downloading starter files for starter_group with id: {group_data['id']}")
            zip_byte_stream = self.api.download_starter_file_entries(self._markus_course_id, self._assignment_id,
                                                                     group_data["id"])
            if not isinstance(zip_byte_stream, bytes):
                sys.stderr.write(f"[MARKUSAPI ERROR] {zip_byte_stream}\n")
                sys.stderr.flush()
                continue
            self._unzip_file(zip_byte_stream, destination)

    def run_moss(self) -> None:
        if os.path.isfile(self.moss_report_url_file) and not self.force:
            return
        starter_files = glob.glob(os.path.join(self.org_starter_files_dir, "*", self.file_glob), recursive=True)
        for i, filename in enumerate(starter_files):
            self._print(f"Sending starter files to MOSS {i + 1}/{len(starter_files)}", end="\r")
            self.moss.addBaseFile(filename, os.path.relpath(filename, self.workdir))
        self._print()
        submission_files = glob.glob(os.path.join(self.submission_files_dir, "*", self.file_glob), recursive=True)
        for i, filename in enumerate(submission_files):
            self._print(f"Sending submission files to MOSS {i + 1}/{len(submission_files)}", end="\r")
            self.moss.addFile(filename, os.path.relpath(filename, self.workdir))
        self._print()
        self._print(f"Running moss")
        self.__moss_report_url = self.moss.send()
        self._print(f"Saving MOSS results from: {self.moss_report_url}")
        os.makedirs(self.moss_report_dir, exist_ok=True)
        with open(self.moss_report_url_file, "w") as f:
            f.write(self.moss_report_url)

    @staticmethod
    def _parse_url(url):
        data = requests.get(url).content.decode()
        return bs4.BeautifulSoup(data, features='html5lib')

    def _localize_page_contents(self, content: BeautifulSoup) -> str:
        """Return a string of content's body, converting all URLs into
        relative paths.
        """
        return str(content).replace(self.__moss_report_url, '.')

    def _moss_download(self, url, dest_dir):
        parsed_html = self._parse_url(url)
        with open(os.path.join(dest_dir, 'index.html'), 'w') as f:
            f.write(self._localize_page_contents(parsed_html))
        urls = {u for u in (a.attrs.get('href') for a in parsed_html.find_all('a')) if u.startswith(url)}
        for url_ in urls:
            parsed_html = self._parse_url(url_)
            with open(os.path.join(dest_dir, os.path.basename(url_)), 'w') as f:
                f.write(self._localize_page_contents(parsed_html))
            for src_url in [f.attrs['src'] for f in parsed_html.find_all('frame')]:
                with open(os.path.join(dest_dir, os.path.basename(src_url)), 'w') as f:
                    f.write(self._localize_page_contents(self._parse_url(os.path.join(url, src_url))))

    def download_moss_report(self) -> None:
        if not os.path.isdir(self.moss_report_download_dir) or self.force:
            self._print(f"Downloading MOSS report")
            os.makedirs(self.moss_report_download_dir, exist_ok=True)
            self._moss_download(self.moss_report_url, self.moss_report_download_dir)

    def write_final_report(self) -> None:
        assignment_report_dir = os.path.join(self.final_report_dir, self.markus_assignment)
        if not os.path.isdir(assignment_report_dir) or self.force:
            self._print(f"Organizing final report for assignment: {self.markus_assignment}")
            os.makedirs(assignment_report_dir, exist_ok=True)
            if os.path.isdir(self.starter_files_dir):
                self._copy_starter_files(assignment_report_dir)
            with open(os.path.join(assignment_report_dir, self.FINAL_REPORT_CASE_OVERVIEW), "w") as overview_f:
                overview_writer = csv.writer(overview_f)
                overview_writer.writerow(self.OVERVIEW_INFO)
                report_iter = self._parse_html_report()
                for i, (match_file, group1, group2, similarity, matched_lines) in enumerate(report_iter):
                    self._print(f"Creating report for groups {group1} and {group2} with {similarity}% similarity.")
                    case = f"case_{i + 1}"
                    case_dir = os.path.join(assignment_report_dir, case)
                    os.makedirs(case_dir, exist_ok=True)
                    self._copy_moss_report(match_file, os.path.join(case_dir, f"moss.html"))
                    groups = [group1, group2]
                    for group in groups:
                        self._copy_submission_files(group, case_dir)
                    self._write_case_report(groups, case_dir)
                    overview_writer.writerow((case, ";".join(groups), similarity, matched_lines))

    @property
    def markus_api_key(self) -> str:
        if self.__markus_api_key is None:
            raise Exception("markus_api_key is required to perform this action")
        return self.__markus_api_key

    @property
    def markus_url(self) -> str:
        if self.__markus_url is None:
            raise Exception("markus_url is required to perform this action")
        return self.__markus_url

    @property
    def markus_assignment(self) -> str:
        if self.__markus_assignment is None:
            raise Exception("markus_assignment is required to perform this action")
        return self.__markus_assignment

    @property
    def moss_userid(self) -> int:
        if self.__moss_userid is None:
            raise Exception("moss_userid is required to perform this action")
        return self.__moss_userid

    @property
    def moss_report_url(self) -> str:
        if self.__moss_report_url is None:
            url = None
            if os.path.isfile(self.moss_report_url_file):
                self._print(f"Attempting to read moss report url from {self.moss_report_url_file}")
                with open(self.moss_report_url_file) as f:
                    url = f.read().strip()
            if url:
                self.__moss_report_url = url
            else:
                raise Exception("moss_report_url is required to perform this action")
        return self.__moss_report_url

    @property
    def workdir(self) -> str:
        if self.__workdir is None:
            raise Exception("workdir is required to perform this action")
        return self.__workdir

    @property
    def language(self) -> str:
        if self.__language is None:
            raise Exception("language is required to perform this action")
        return self.__language

    @property
    def submission_files_dir(self) -> str:
        return os.path.join(self.workdir, self.SUBMISSION_FILES_DIR)

    @property
    def pdf_submission_files_dir(self) -> str:
        return os.path.join(self.workdir, self.PDF_SUBMISSION_FILES_DIR)

    @property
    def pdf_starter_files_dir(self) -> str:
        return os.path.join(self.workdir, self.STARTER_FILES_DIR, 'pdf')

    @property
    def org_starter_files_dir(self) -> str:
        return os.path.join(self.workdir, self.STARTER_FILES_DIR, 'org')

    @property
    def starter_files_dir(self) -> str:
        return os.path.join(self.workdir, self.STARTER_FILES_DIR)

    @property
    def moss_report_dir(self) -> str:
        return os.path.join(self.workdir, self.MOSS_REPORT_DIR)

    @property
    def moss_report_url_file(self) -> str:
        return os.path.join(self.moss_report_dir, self.MOSS_REPORT_URL)

    @property
    def moss_report_download_dir(self) -> str:
        return os.path.join(self.moss_report_dir, self.MOSS_REPORT_DOWNLOAD)

    @property
    def final_report_dir(self) -> str:
        return os.path.join(self.workdir, self.FINAL_REPORT_DIR)

    @property
    def selected_cases_dir(self) -> str:
        return os.path.join(self.workdir, self.SELECTED_CASES_DIR)

    @property
    def api(self) -> markusapi.Markus:
        if self.__api is None:
            self.__api = markusapi.Markus(url=self.markus_url, api_key=self.markus_api_key)
        return self.__api

    @property
    def moss(self) -> mosspy.Moss:
        if self.__moss is None:
            self.__moss = mosspy.Moss(self.moss_userid, self.language)
        return self.__moss

    @property
    def _group_data(self) -> Dict:
        if self.__group_data is None:
            group_data = self.api.get_groups(self._markus_course_id, self._assignment_id)
            if self.groups is not None:
                group_data = [g for g in group_data if g['group_name'] in self.groups]
            self.__group_data = group_data
        return self.__group_data

    @property
    def _membership_data(self) -> Dict:
        if self.__membership_data is None:
            self.__membership_data = self._get_group_membership_info()
        return self.__membership_data

    @property
    def _assignment_id(self) -> int:
        if self.__assignment_id is None:
            self.__assignment_id = self._find_assignment_id()
        return self.__assignment_id

    @property
    def _markus_course_id(self) -> int:
        if self.__markus_course_id is None:
            self.__markus_course_id = self._find_course_id()
        return self.__markus_course_id

    @property
    def _starter_file_groups(self) -> Dict:
        if self.__starter_file_groups is None:
            self.__starter_file_groups = self.api.get_starter_file_groups(self._markus_course_id, self._assignment_id)
        return self.__starter_file_groups

    @property
    def _pandoc(self) -> str:
        pandoc = shutil.which("pandoc")
        if pandoc is None:
            raise Exception(f"No 'pandoc' executable found in the path. Pandoc is required to run this action.")
        return pandoc

    @property
    def _report_regex(self) -> Pattern:
        if self.__report_regex is None:
            self.__report_regex = re.compile(rf"{self.SUBMISSION_FILES_DIR}/([^/]+)/(.*)\s\((\d+)\%\)")
        return self.__report_regex

    @staticmethod
    def _clean_filename(filename) -> str:
        return filename.replace(" ", "_")

    def _print(self, *args, **kwargs) -> None:
        if self.verbose:
            print(self.PRINT_PREFIX, *args, **kwargs)

    def _find_assignment_id(self) -> int:
        short_ids = []
        assignment_data = self.api.get_assignments(self._markus_course_id)
        for data in assignment_data:
            short_ids.append(data.get("short_identifier"))
            if data.get("short_identifier") == self.markus_assignment:
                return data["id"]
        msg = f"No MarkUs assignment found with short identifier: {self.markus_assignment}\noptions:{short_ids}"
        raise Exception(msg)

    def _find_course_id(self) -> int:
        short_ids = []
        course_data = self.api.get_all_courses()
        for data in course_data:
            short_ids.append(data.get("name"))
            if data.get("name") == self.__markus_course:
                return data["id"]
        msg = f"No MarkUs course found with name: {self.markus_course}\noptions:{short_ids}"
        raise Exception(msg)

    def _get_group_membership_info(self) -> Dict:
        user_info = {u["id"]: {k: u.get(k) for k in self.USER_INFO} for u in
                     self.api.get_all_roles(self._markus_course_id)}
        members = collections.defaultdict(list)
        for data in self._group_data:
            for role_id in (m["role_id"] for m in data["members"]):
                user_info[role_id]["group_name"] = data["group_name"]
                members[data["group_name"]].append(user_info[role_id])
        return members

    def _copy_files_to_pdf(self, source_dir: str, dest_dir: str) -> None:
        for source_file in glob.iglob(os.path.join(source_dir, "*", self.file_glob), recursive=True):
            rel_source = os.path.relpath(source_file, source_dir)
            rel_destination = self._file_to_pdf(rel_source)
            abs_destination = os.path.join(dest_dir, rel_destination)
            if self._copy_file_to_pdf(source_file, abs_destination):
                self._print(f"Converting {rel_source} to pdf: {rel_destination}")

    def _copy_file_to_pdf(self, source_file: str, destination: str) -> bool:
        if os.path.isfile(source_file) and (not os.path.isfile(destination) or self.force):
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            proc = subprocess.Popen(
                [self._pandoc,
                 "--pdf-engine=xelatex",
                 "-H", self.latex_preamble,
                 "-V", "geometry:margin=1cm",
                 "-V", "pagestyle=empty",
                 "-o", destination],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            with open(source_file, "r") as f:
                filename = os.path.split(source_file)[-1]
                content = b"# %b\n\n```{.%b .numberLines}\n%b\n```" % (
                    filename.encode(errors="replace"), self.language.encode(),
                    f.read()).encode()
            _out, err = proc.communicate(content)
            if proc.returncode != 0:
                sys.stderr.write(f"[PANDOC ERROR]{err}\n")
                sys.stderr.flush()
            return True
        return False

    def _parse_html_report(self) -> Iterator[Tuple[str, str, str, int, int]]:
        with open(os.path.join(self.moss_report_download_dir, "index.html")) as f:
            parsed_html = bs4.BeautifulSoup(f, features='html5lib')
            for row in parsed_html.body.find("table").find_all("tr"):
                if row.find("th"):
                    continue
                submission1, submission2, lines = row.find_all("td")
                match_file = os.path.join(
                    self.moss_report_download_dir, os.path.basename(submission1.find("a").get("href"))
                )
                matched_lines = int(lines.string.strip())
                group1, matched_file, similarity = re.match(self._report_regex, submission1.find("a").string).groups()
                group2, _, _ = re.match(self._report_regex, submission2.find("a").string).groups()
                yield match_file, group1, group2, similarity, matched_lines

    def _copy_submission_files(self, group: str, destination: str) -> None:
        for abs_file in glob.iglob(os.path.join(self.submission_files_dir, group, self.file_glob), recursive=True):
            rel_file = os.path.relpath(abs_file, self.submission_files_dir)
            rel_pdf = self._file_to_pdf(rel_file)
            abs_pdf = os.path.join(self.pdf_submission_files_dir, rel_pdf)
            file_dest = os.path.join(destination, group, "org", os.path.relpath(rel_file, group))
            pdf_dest = os.path.join(destination, group, "pdf", os.path.relpath(rel_pdf, group))
            os.makedirs(os.path.dirname(file_dest), exist_ok=True)
            os.makedirs(os.path.dirname(pdf_dest), exist_ok=True)
            self._copy_file(abs_file, file_dest)
            self._copy_file(abs_pdf, pdf_dest)

    def _copy_starter_files(self, destination: str) -> None:
        shutil.copytree(self.starter_files_dir, os.path.join(destination, self.STARTER_FILES_DIR), dirs_exist_ok=True)

    def _write_case_report(self, groups: Iterable[str], destination: str) -> None:
        for group in groups:
            group_membership_file = os.path.join(destination, group, self.GROUP_MEMBERSHIP_FILE)
            os.makedirs(os.path.join(destination, group), exist_ok=True)
            with open(group_membership_file, "w") as f:
                writer = csv.DictWriter(f, fieldnames=self.USER_INFO)
                writer.writeheader()
                for data in self._membership_data[group]:
                    writer.writerow(data)

    @staticmethod
    def _get_group_pair(group_members: set[str], group_pairs: list[set[str]]) -> set[str] | None:
        """Return the group pair containing all members in group_members, or None if there
        is no such group.

        If there are multiple group pairs containing all members, the first is returned.
        """
        for pair in group_pairs:
            if group_members.issubset(pair):
                return pair
        return None

    @staticmethod
    def _get_cases_to_groups(case_overview_path: str,
                             group_pairs: list[set[str]],
                             matches: set[str]) -> dict[str, set[str]]:
        """Return a dictionary mapping matches to groups for the given group_pairs
        or matches based on the information in the file located at case_overview_path.

        For matches involving the provided groups, update the list of matches.

        Precondition:
        - case_overview_path is the filename of a case overview CSV generated by
          write_final_report.
        """
        cases_to_groups = {}

        # Populate a dictionary mapping the relevant cases to the group(s)
        with open(case_overview_path) as f:
            reader = csv.reader(f)
            for row in reader:
                case, groups = row[:2]
                groups = set(groups.split(';'))
                group_pair = MarkusMoss._get_group_pair(groups, group_pairs)
                if group_pair or case in matches:
                    cases_to_groups[case] = group_pair if group_pair else groups
                    matches.add(case)

        return cases_to_groups

    @staticmethod
    def _copy_file(source: str, dest: str) -> None:
        try:
            shutil.copy(source, dest)
        except FileNotFoundError as e:
            sys.stderr.write(f"{e}\n")
            sys.stderr.flush()

    @staticmethod
    def _file_to_pdf(source: str) -> str:
        return f"{source}.pdf"

    def _copy_moss_report(self, base_html_file: str, destination: str) -> None:
        base, _ = os.path.splitext(base_html_file)
        base_basename = os.path.basename(base)
        top = f"{base}-top.html"
        with open(os.path.join(os.path.dirname(__file__), 'templates', 'report_template.html')) as f:
            template = bs4.BeautifulSoup(f, features='html5lib')
        with open(base_html_file) as f:
            base_html = bs4.BeautifulSoup(f, features='html5lib')
            title = base_html.head.find("title").text
            template.head.find('title').string = title
        with open(top) as f:
            top_html = bs4.BeautifulSoup(f, features='html5lib')
            table = top_html.body.find("center")
            for a in table.find_all('a'):
                href = os.path.basename(a["href"])
                match_file, match_num = re.match(rf'{base_basename}-([01])\.html#(\d+)', href).groups()
                a["href"] = f"#match-{match_file}-{match_num}"
                a["target"] = "_self"
            top_div = template.body.find('div', {"id": "top"})
            top_div.append(table)
        for match_i in range(2):
            match_file = f"{base}-{match_i}.html"
            with open(match_file) as f:
                match_html = bs4.BeautifulSoup(f, features='html5lib')
                match_body = match_html.body
                for a in match_body.find_all('a'):
                    if a.get("href"):
                        match_file, match_num = re.match(rf'{base_basename}-([01])\.html#(\d+)', a["href"]).groups()
                        a["href"] = f"#match-{match_file}-{match_num}"
                        a["target"] = "_self"
                    if a.get("name"):
                        a["id"] = f"match-{match_i}-{a['name']}"
            match_div = template.body.find('div', {"id": f"match-{match_i}"})
            file_title = template.new_tag('h3')
            match_div.append(file_title)
            match_div.append(match_body)
        with open(destination, 'w') as f:
            f.write(str(template))

    @property
    def _html_comparison_template(self):
        return """<h2>Match {match}</h2>
        <table><tr><th>{header1}</th><th>{header2}</th></tr>
        {rows}
        </table>"""

    @property
    def _html_row_template(self):
        return ("""<tr>
        <td><pre class="{language} numberLines" firstnumber="{{code1_start}}">{{code1}}</pre></td>
        <td><pre class="{language} numberLines" firstnumber="{{code2_start}}">{{code2}}</pre></td>
        </tr>""").format(language=self.language)

    @staticmethod
    def _combine_html_list(html_list: list[str]):
        """Return html_list combined with <br/> tags and encased in <html> tags."""
        combined = "<br/>".join(html_list)
        return f"<html>{combined}</html>"

    def _html_code_rows(self, code1: str, code2: str,
                        code1_start: int = 1, code2_start: int = 1) -> str:
        """Return the rows corresponding to the code block pairs code1 and code2.

        Rows are limited to CODE_BLOCK_LIMIT lines each.
        """
        code1_rows = code1.split("\n")
        code2_rows = code2.split("\n")
        rows = []
        while code1_rows or code2_rows:
            c1 = code1_rows[:self.CODE_BLOCK_LIMIT]
            code1_rows = code1_rows[self.CODE_BLOCK_LIMIT:]
            c2 = code2_rows[:self.CODE_BLOCK_LIMIT]
            code2_rows = code2_rows[self.CODE_BLOCK_LIMIT:]
            rows.append(self._html_row_template.format(code1_start=code1_start,
                                                       code1="\n".join(c1),
                                                       code2_start=code2_start,
                                                       code2="\n".join(c2)))

            code1_start += len(c1)
            code2_start += len(c2)
        return "\n".join(rows)

    def _match_to_html(self, match_number: int, header1: str, header2: str,
                       code1: str, code2: str,
                       code1_start: int = 1,
                       code2_start: int = 1) -> str:
        """Return an HTML excerpt in the format:

        Match <match_number>
        header1    |  header 2
        ----------------------
        code1      | code2

        To be formatted in pandoc and turned into a PDF.
        """
        out = self._html_comparison_template.format(
            match=match_number, header1=header1, header2=header2,
            rows=self._html_code_rows(code1=code1.replace("<", "&lt;").replace(">", "&gt;"),
                                      code2=code2.replace("<", "&lt;").replace(">", "&gt;"),
                                      code1_start=code1_start,
                                      code2_start=code2_start)
        )

        return out

    @property
    def comparison_preamble(self):
        return os.path.join(os.path.dirname(__file__), 'templates', 'comparison_preamble.tex')

    @property
    def latex_preamble(self):
        return os.path.join(os.path.dirname(__file__), 'templates', 'latex_preamble.tex')

    @property
    def highlight_lua(self):
        return os.path.join(os.path.dirname(__file__), 'templates', 'highlight.lua')

    @property
    def comparison_vars(self):
        return os.path.join(os.path.dirname(__file__), 'templates', 'comparison_vars.json')

    def _html_to_pdf(self, html_str: str, destination: str, landscape: bool = False) -> bool:
        """Write html_str to a pandoc-formatted PDF at <destination>.

        Precondition: html_str is valid HTML.
        """
        geometry = ("-V", "geometry:margin=1cm,landscape") if landscape else \
            ("-V", "geometry:margin=1cm")

        if not os.path.isfile(destination) or self.force:
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            proc = subprocess.Popen(
                [self._pandoc,
                 "--pdf-engine=xelatex",
                 "--metadata-file", self.comparison_vars,
                 "--listings",
                 f"--lua-filter={self.highlight_lua}",
                 "-H", self.latex_preamble,
                 "-H", self.comparison_preamble,
                 "-f", "html",
                 "-o", destination,
                 *geometry],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            _out, err = proc.communicate(html_str.encode())
            if proc.returncode != 0:
                sys.stderr.write(f"[PANDOC ERROR]{err}\n")
                sys.stderr.flush()
            return True
        return False

    def _make_group_cover(self, group_name: str, output_dir: str = None) -> str:
        """Generate a pdf named <group_name>_cover.pdf that contains:
        - The group name
        - Membership information of group members

        Return the name of the PDF generated. If <group_name>_cover.pdf already
        exists, do nothing.
        """
        dest = os.path.join(output_dir if output_dir else self.selected_cases_dir,
                            f"{group_name}_cover.pdf")

        if os.path.exists(dest):
            return dest

        proc = subprocess.Popen(
            [self._pandoc,
             "--pdf-engine=xelatex",
             "--listing",
             "-H", self.latex_preamble,
             "-V", "geometry:margin=1cm",
             "-V", "pagestyle=empty",
             "-o", dest],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        membership_info = self._membership_data[group_name]
        member_strings = [(f"- {member['first_name']} {member['last_name']} "
                           f"({member['user_name']} - {member['id_number']} - {member['email']})")
                          for member in membership_info]
        member_string = "\n".join(member_strings)
        content = f"# {group_name}\n\n{member_string}".encode()

        _out, err = proc.communicate(content)
        if proc.returncode != 0:
            sys.stderr.write(f"[PANDOC ERROR]{err}\n")
            sys.stderr.flush()

        return dest

    @staticmethod
    def get_path_from_header(moss_header: str) -> str:
        """From a moss header, return the filepath for the given file.
        """
        header_re = r"(.*) \([0-9]*%\)"
        return re.match(header_re, moss_header).group(1)

    @staticmethod
    def _get_group_and_file_from_path(file_path: str) -> tuple[str, str]:
        """From a moss header, return the filepath for the given file.
        """
        details_re = r"[^[\\//]*[\\//]([^[\\//]*)[\\//](.*)"
        group_name, filename = re.match(details_re, file_path).groups()
        return group_name, filename

    def format_header(self, moss_header: str) -> str:
        """Return a header in the format:
            group name (members, if different) - file
        Given a header from moss' table.

        If the moss_header cannot be interpreted, then the original header
        is returned as is.
        """
        try:
            file_path = self.get_path_from_header(moss_header)
            group_name, filename = self._get_group_and_file_from_path(file_path)

            group_members = self._membership_data[group_name]
            members = ", ".join([
                "{} {}".format(member['first_name'], member['last_name'])
                for member in group_members
            ])

            if len(group_members) > 1:
                members = f"{group_name} ({members})"

            return f"{members}'s {filename}"
        except:
            return moss_header

    def extract_matches(self, case_file: str) -> _Case:
        """Return a _Case containing all details from case_file.
        """
        return _Case(case_file, self)

    def _get_select_groups_matches(self) -> tuple[list[set[str]], set[str]]:
        """Return a tuple where the first element is a list of group name sets
        and the second is a list of matches.

        This information should be based on the information provided in self.select
        (i.e. passed in with -s / --selected-groups.)

        Precondition:
        - The elements in self.select are either an integer representing a case number
          (or case_#), or a list where all elements are group names
        """
        groups = []
        matches = set()

        for item in self.selected_groups:
            if not isinstance(item, list):
                matches.add(f'case_{item}' if 'case_' not in item else item)
            else:
                groups.append(set(item))

        return groups, matches

    def select_match(self) -> None:
        """Add the selected match into the 'selected' (SELECTED_CASES_DIR) directory.
        A subfolder will be created with the name <group1>_<group2> where
        <group_1/2> are the names of the groups in the match. This subfolder
        will contain:
        - <group1>.pdf: A single combined PDF containing all of <group1>'s files
        - <group2>.pdf: A single combined PDF containing all of <group2>'s files
        - match_###.pdf: A single PDF comparing the match(es) between the two groups

        Preconditions:
        - write_final_report has been run
        """
        assignment_report_dir = os.path.join(self.final_report_dir, self.markus_assignment)
        if not os.path.exists(assignment_report_dir):
            sys.exit("write_final_report needs to be run before matches can be selected.")

        case_overview = os.path.join(assignment_report_dir, self.FINAL_REPORT_CASE_OVERVIEW)
        cases_to_groups = self._get_cases_to_groups(case_overview,
                                                    *self._get_select_groups_matches())

        # Maps cases to a tuple of two lists:
        # The first list is for each group's contents, the second is for
        # the case PDFs
        report_pdfs = {}

        # Maps cases to a dictionary mapping filenames to _HighlightedFiles
        groups_to_files_to_highlights = {}

        # 1. Find all of the matches and get the basic folder structure setup
        for case in cases_to_groups:
            case_number = case.split("_")[-1]
            groups = sorted(cases_to_groups[case])
            case_dir = "_".join(groups)
            if case_dir not in report_pdfs:
                report_pdfs[case_dir] = ({}, [])

            if case_dir not in groups_to_files_to_highlights:
                groups_to_files_to_highlights[case_dir] = {}

            files_to_highlights = groups_to_files_to_highlights[case_dir]

            # Create a directory for the pair of groups named <group1>_<group2>
            # where the group names are in alphabetical order
            select_case_dir = os.path.join(self.selected_cases_dir, case_dir)
            case_final_report_dir = os.path.join(assignment_report_dir, case)
            os.makedirs(select_case_dir, exist_ok=True)

            # If it doesn't exist already, copy directories from each group's final report
            # (containing all of the group info, original files, and individual PDFs)
            for group in groups:
                group_file_path = os.path.join(case_final_report_dir, group)
                if os.path.exists(group_file_path):
                    shutil.copytree(group_file_path,
                                    os.path.join(select_case_dir, group),
                                    dirs_exist_ok=True)

                # Add all of the code files to the group's files.
                if group not in report_pdfs[case_dir][0]:
                    cover_path = self._make_group_cover(group, select_case_dir)
                    report_pdfs[case_dir][0][group] = _GroupFiles(group, cover_path,
                                                                  members=self._membership_data[group])

                short_path = os.path.join(group, "org")
                code_file_path = os.path.join(select_case_dir, short_path)
                if os.path.exists(code_file_path):
                    for filename in os.listdir(code_file_path):
                        path = os.path.join(code_file_path, filename)
                        rel_path = os.path.join('submission_files', group, filename)
                        files_to_highlights[rel_path] = _HighlightedFile(filename,
                                                                         path,
                                                                         self.language)

            # Extract all matches and related information, generating the relevant PDFs
            # Copy the match's moss.html and rename it to case_#.html
            new_case_location = os.path.join(select_case_dir, f"{case}.html")
            shutil.copy(os.path.join(case_final_report_dir, "moss.html"),
                        new_case_location)

            # Create a PDF for the match
            match_details = self.extract_matches(new_case_location)
            case_name = match_details.get_name()
            case_name = case_name if case_name else case_number
            all_html = [f"<h1>{case_name}</h1>"]

            current_match_number = 1
            for i in range(len(match_details)):
                # Skip the match if it's an exclusion
                if case_number in self.exclude_matches and i in self.exclude_matches[case_number]:
                    continue

                match_pair = match_details[i]
                match1, match2 = match_pair
                for (fp, _, start, end, _) in match_pair:
                    if fp not in files_to_highlights:
                        groupname, short_path = self._get_group_and_file_from_path(fp)
                        content_path = os.path.join(select_case_dir, groupname, "org", short_path)
                        files_to_highlights[fp] = _HighlightedFile(short_path,
                                                                   content_path,
                                                                   self.language)
                    files_to_highlights[fp].add_highlight(start, end)
                all_html.append(self._match_to_html(current_match_number, match1.header, match2.header,
                                                    match1.code, match2.code, match1.start, match2.start))
                current_match_number += 1

            combined_html = self._combine_html_list(all_html)

            case_pdf = os.path.join(select_case_dir, f"{case}.pdf")
            self._html_to_pdf(combined_html, case_pdf)
            # Add the case PDF to the list of PDFs
            report_pdfs[case_dir][1].append((case_name, case_pdf))

        # 2. For any files that were involved in matches, highlight the relevant code.
        for case_dir in groups_to_files_to_highlights:
            select_case_dir = os.path.join(self.selected_cases_dir, case_dir)
            fth = groups_to_files_to_highlights[case_dir]
            for filepath in fth:
                group, filename = self._get_group_and_file_from_path(filepath)
                highlight: _HighlightedFile
                highlight = fth[filepath]
                pdf_path = os.path.join(select_case_dir,
                                        f"{group}_{highlight.filename}.pdf")
                self._html_to_pdf(highlight.make_html(), pdf_path)
                report_pdfs[case_dir][0][group].add_file(filename, pdf_path, override=True)

        import toc_pdf_merge
        for case_dir in report_pdfs:
            select_case_dir = os.path.join(self.selected_cases_dir, case_dir)
            group_files, cases = report_pdfs[case_dir]
            pm = toc_pdf_merge.PDFMerger()
            for group in sorted(group_files.keys()):
                group_files[group].add_to_merger(pm)

            for case in cases:
                title, filename = case
                pm.add_pdf(toc_pdf_merge.IncludePDF(filename, title))

            pm.make(self.SELECT_OUTPUT_REPORT, destination_folder=select_case_dir)

            # Clean up all PDFs aside from the final report
            for filename in os.listdir(select_case_dir):
                if filename.endswith(".pdf") and filename != f"{self.SELECT_OUTPUT_REPORT}.pdf":
                    os.remove(os.path.join(select_case_dir, filename))

    @staticmethod
    def _unzip_file(zip_byte_stream: bytes, destination: str) -> None:
        with zipfile.ZipFile(io.BytesIO(zip_byte_stream)) as zf:
            for fname in zf.namelist():
                *dpaths, bname = fname.split(os.sep)
                dest = os.path.join(destination, *dpaths[1:])
                filename = os.path.join(dest, bname)
                if filename.endswith("/"):
                    os.makedirs(filename, exist_ok=True)
                else:
                    os.makedirs(dest, exist_ok=True)
                    with open(filename, "wb") as f:
                        f.write(zf.read(fname))

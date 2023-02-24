# Licensed under the Apache License: http://www.apache.org/licenses/LICENSE-2.0
# For details: https://github.com/nedbat/coveragepy/blob/master/NOTICE.txt

"""XML reporting for coverage.py"""

from __future__ import annotations

import os
import os.path
import sys
import time
import xml.dom.minidom

from dataclasses import dataclass
from typing import Any, Dict, IO, Iterable, Optional, TYPE_CHECKING, cast

from coverage import __version__, files
from coverage.misc import isolate_module, human_sorted, human_sorted_items
from coverage.plugin import FileReporter
from coverage.report import get_analysis_to_report
from coverage.results import Analysis
from coverage.types import TMorf
from coverage.version import __url__

if TYPE_CHECKING:
    from coverage import Coverage

os = isolate_module(os)


DTD_URL = 'https://raw.githubusercontent.com/cobertura/web/master/htdocs/xml/coverage-04.dtd'


def rate(hit: int, num: int) -> str:
    """Return the fraction of `hit`/`num`, as a string."""
    if num == 0:
        return "1"
    else:
        return "%.4g" % (hit / num)


@dataclass
class PackageData:
    """Data we keep about each "package" (in Java terms)."""
    elements: Dict[str, xml.dom.minidom.Element]
    hits: int
    lines: int
    br_hits: int
    branches: int


def appendChild(parent: Any, child: Any) -> None:
    """Append a child to a parent, in a way mypy will shut up about."""
    parent.appendChild(child)


class XmlReporter:
    """A reporter for writing Cobertura-style XML coverage results."""

    report_type = "XML report"

    def __init__(self, coverage: Coverage) -> None:
        self.coverage = coverage
        self.config = self.coverage.config

        self.source_paths = set()
        if self.config.source:
            for src in self.config.source:
                if os.path.exists(src):
                    if not self.config.relative_files:
                        src = files.canonical_filename(src)
                    self.source_paths.add(src)
        self.packages: Dict[str, PackageData] = {}
        self.xml_out: xml.dom.minidom.Document

    def report(self, morfs: Optional[Iterable[TMorf]], outfile: Optional[IO[str]] = None) -> float:
        """Generate a Cobertura-compatible XML report for `morfs`.

        `morfs` is a list of modules or file names.

        `outfile` is a file object to write the XML to.

        """
        # Initial setup.
        outfile = outfile or sys.stdout
        has_arcs = self.coverage.get_data().has_arcs()

        # Create the DOM that will store the data.
        impl = xml.dom.minidom.getDOMImplementation()
        assert impl is not None
        self.xml_out = impl.createDocument(None, "coverage", None)

        # Write header stuff.
        xcoverage = self.xml_out.documentElement
        xcoverage.setAttribute("version", __version__)
        xcoverage.setAttribute("timestamp", str(int(time.time()*1000)))
        xcoverage.appendChild(self.xml_out.createComment(
            f" Generated by coverage.py: {__url__} "
        ))
        xcoverage.appendChild(self.xml_out.createComment(f" Based on {DTD_URL} "))

        # Call xml_file for each file in the data.
        for fr, analysis in get_analysis_to_report(self.coverage, morfs):
            self.xml_file(fr, analysis, has_arcs)

        xsources = self.xml_out.createElement("sources")
        xcoverage.appendChild(xsources)

        # Populate the XML DOM with the source info.
        for path in human_sorted(self.source_paths):
            xsource = self.xml_out.createElement("source")
            appendChild(xsources, xsource)
            txt = self.xml_out.createTextNode(path)
            appendChild(xsource, txt)

        lnum_tot, lhits_tot = 0, 0
        bnum_tot, bhits_tot = 0, 0

        xpackages = self.xml_out.createElement("packages")
        xcoverage.appendChild(xpackages)

        # Populate the XML DOM with the package info.
        for pkg_name, pkg_data in human_sorted_items(self.packages.items()):
            xpackage = self.xml_out.createElement("package")
            appendChild(xpackages, xpackage)
            xclasses = self.xml_out.createElement("classes")
            appendChild(xpackage, xclasses)
            for _, class_elt in human_sorted_items(pkg_data.elements.items()):
                appendChild(xclasses, class_elt)
            xpackage.setAttribute("name", pkg_name.replace(os.sep, '.'))
            xpackage.setAttribute("line-rate", rate(pkg_data.hits, pkg_data.lines))
            if has_arcs:
                branch_rate = rate(pkg_data.br_hits, pkg_data.branches)
            else:
                branch_rate = "0"
            xpackage.setAttribute("branch-rate", branch_rate)
            xpackage.setAttribute("complexity", "0")

            lhits_tot += pkg_data.hits
            lnum_tot += pkg_data.lines
            bhits_tot += pkg_data.br_hits
            bnum_tot += pkg_data.branches

        xcoverage.setAttribute("lines-valid", str(lnum_tot))
        xcoverage.setAttribute("lines-covered", str(lhits_tot))
        xcoverage.setAttribute("line-rate", rate(lhits_tot, lnum_tot))
        if has_arcs:
            xcoverage.setAttribute("branches-valid", str(bnum_tot))
            xcoverage.setAttribute("branches-covered", str(bhits_tot))
            xcoverage.setAttribute("branch-rate", rate(bhits_tot, bnum_tot))
        else:
            xcoverage.setAttribute("branches-covered", "0")
            xcoverage.setAttribute("branches-valid", "0")
            xcoverage.setAttribute("branch-rate", "0")
        xcoverage.setAttribute("complexity", "0")

        # Write the output file.
        outfile.write(serialize_xml(self.xml_out))

        # Return the total percentage.
        denom = lnum_tot + bnum_tot
        if denom == 0:
            pct = 0.0
        else:
            pct = 100.0 * (lhits_tot + bhits_tot) / denom
        return pct

    def xml_file(self, fr: FileReporter, analysis: Analysis, has_arcs: bool) -> None:
        """Add to the XML report for a single file."""

        if self.config.skip_empty:
            if analysis.numbers.n_statements == 0:
                return

        # Create the 'lines' and 'package' XML elements, which
        # are populated later.  Note that a package == a directory.
        filename = fr.filename.replace("\\", "/")
        for source_path in self.source_paths:
            if not self.config.relative_files:
                source_path = files.canonical_filename(source_path)
            if filename.startswith(source_path.replace("\\", "/") + "/"):
                rel_name = filename[len(source_path)+1:]
                break
        else:
            rel_name = fr.relative_filename()
            self.source_paths.add(fr.filename[:-len(rel_name)].rstrip(r"\/"))

        dirname = os.path.dirname(rel_name) or "."
        dirname = "/".join(dirname.split("/")[:self.config.xml_package_depth])
        package_name = dirname.replace("/", ".")

        package = self.packages.setdefault(package_name, PackageData({}, 0, 0, 0, 0))

        xclass: xml.dom.minidom.Element = self.xml_out.createElement("class")

        appendChild(xclass, self.xml_out.createElement("methods"))

        xlines = self.xml_out.createElement("lines")
        appendChild(xclass, xlines)

        xclass.setAttribute("name", os.path.relpath(rel_name, dirname))
        xclass.setAttribute("filename", rel_name.replace("\\", "/"))
        xclass.setAttribute("complexity", "0")

        branch_stats = analysis.branch_stats()
        missing_branch_arcs = analysis.missing_branch_arcs()

        # For each statement, create an XML 'line' element.
        for line in sorted(analysis.statements):
            xline = self.xml_out.createElement("line")
            xline.setAttribute("number", str(line))

            # Q: can we get info about the number of times a statement is
            # executed?  If so, that should be recorded here.
            xline.setAttribute("hits", str(int(line not in analysis.missing)))

            if has_arcs:
                if line in branch_stats:
                    total, taken = branch_stats[line]
                    xline.setAttribute("branch", "true")
                    xline.setAttribute(
                        "condition-coverage",
                        "%d%% (%d/%d)" % (100*taken//total, taken, total)
                    )
                if line in missing_branch_arcs:
                    annlines = ["exit" if b < 0 else str(b) for b in missing_branch_arcs[line]]
                    xline.setAttribute("missing-branches", ",".join(annlines))
            appendChild(xlines, xline)

        class_lines = len(analysis.statements)
        class_hits = class_lines - len(analysis.missing)

        if has_arcs:
            class_branches = sum(t for t, k in branch_stats.values())
            missing_branches = sum(t - k for t, k in branch_stats.values())
            class_br_hits = class_branches - missing_branches
        else:
            class_branches = 0
            class_br_hits = 0

        # Finalize the statistics that are collected in the XML DOM.
        xclass.setAttribute("line-rate", rate(class_hits, class_lines))
        if has_arcs:
            branch_rate = rate(class_br_hits, class_branches)
        else:
            branch_rate = "0"
        xclass.setAttribute("branch-rate", branch_rate)

        package.elements[rel_name] = xclass
        package.hits += class_hits
        package.lines += class_lines
        package.br_hits += class_br_hits
        package.branches += class_branches


def serialize_xml(dom: xml.dom.minidom.Document) -> str:
    """Serialize a minidom node to XML."""
    return cast(str, dom.toprettyxml())

# Copyright 2013-2023 Lawrence Livermore National Security, LLC and other
# Spack Project Developers. See the top-level COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)

import sys
import textwrap
from itertools import zip_longest

import llnl.util.tty as tty
import llnl.util.tty.color as color
from llnl.util.tty.colify import colify

import spack.cmd.common.arguments as arguments
import spack.deptypes as dt
import spack.fetch_strategy as fs
import spack.install_test
import spack.repo
import spack.spec
from spack.package_base import preferred_version

description = "get detailed information on a particular package"
section = "basic"
level = "short"

header_color = "@*b"
plain_format = "@."


def padder(str_list, extra=0):
    """Return a function to pad elements of a list."""
    length = max(len(str(s)) for s in str_list) + extra

    def pad(string):
        string = str(string)
        padding = max(0, length - len(string))
        return string + (padding * " ")

    return pad


def setup_parser(subparser):
    subparser.add_argument(
        "-a", "--all", action="store_true", default=False, help="output all package information"
    )

    options = [
        ("--detectable", print_detectable.__doc__),
        ("--maintainers", print_maintainers.__doc__),
        ("--no-dependencies", "do not " + print_dependencies.__doc__),
        ("--no-variants", "do not " + print_variants.__doc__),
        ("--no-versions", "do not " + print_versions.__doc__),
        ("--phases", print_phases.__doc__),
        ("--tags", print_tags.__doc__),
        ("--tests", print_tests.__doc__),
        ("--virtuals", print_virtuals.__doc__),
    ]
    for opt, help_comment in options:
        subparser.add_argument(opt, action="store_true", help=help_comment)

    arguments.add_common_arguments(subparser, ["package"])


def section_title(s):
    return header_color + s + plain_format


def version(s):
    return spack.spec.VERSION_COLOR + s + plain_format


def variant(s):
    return spack.spec.ENABLED_VARIANT_COLOR + s + plain_format


class VariantFormatter:
    def __init__(self, pkg):
        self.variants = pkg.variants
        self.headers = ("Name [Default]", "When", "Allowed values", "Description")

        # Don't let name or possible values be less than max widths
        _, cols = tty.terminal_size()
        max_name = min(self.column_widths[0], 30)
        max_when = min(self.column_widths[1], 30)
        max_vals = min(self.column_widths[2], 20)

        # allow the description column to extend as wide as the terminal.
        max_description = min(
            self.column_widths[3],
            # min width 70 cols, 14 cols of margins and column spacing
            max(cols, 70) - max_name - max_vals - 14,
        )
        self.column_widths = (max_name, max_when, max_vals, max_description)

        # Compute the format
        self.fmt = "%%-%ss%%-%ss%%-%ss%%s" % (
            self.column_widths[0] + 4,
            self.column_widths[1] + 4,
            self.column_widths[2] + 4,
        )

    def default(self, v):
        s = "on" if v.default is True else "off"
        if not isinstance(v.default, bool):
            s = v.default
        return s

    @property
    def lines(self):
        if not self.variants:
            yield "    None"
            return

        else:
            yield "    " + self.fmt % self.headers
            underline = tuple([w * "=" for w in self.column_widths])
            yield "    " + self.fmt % underline
            yield ""
            for k, e in sorted(self.variants.items()):
                v, w = e
                name = textwrap.wrap(
                    "{0} [{1}]".format(k, self.default(v)), width=self.column_widths[0]
                )
                if all(spec == spack.spec.Spec() for spec in w):
                    w = "--"
                when = textwrap.wrap(str(w), width=self.column_widths[1])
                allowed = v.allowed_values.replace("True, False", "on, off")
                allowed = textwrap.wrap(allowed, width=self.column_widths[2])
                description = []
                for d_line in v.description.split("\n"):
                    description += textwrap.wrap(d_line, width=self.column_widths[3])
                for t in zip_longest(name, when, allowed, description, fillvalue=""):
                    yield "    " + self.fmt % t


def print_dependencies(pkg):
    """output build, link, and run package dependencies"""

    for deptype in ("build", "link", "run"):
        color.cprint("")
        color.cprint(section_title("%s Dependencies:" % deptype.capitalize()))
        deps = sorted(pkg.dependencies_of_type(dt.flag_from_string(deptype)))
        if deps:
            colify(deps, indent=4)
        else:
            color.cprint("    None")


def print_detectable(pkg):
    """output information on external detection"""

    color.cprint("")
    color.cprint(section_title("Externally Detectable: "))

    # If the package has an 'executables' of 'libraries' field, it
    # can detect an installation
    if hasattr(pkg, "executables") or hasattr(pkg, "libraries"):
        find_attributes = []
        if hasattr(pkg, "determine_version"):
            find_attributes.append("version")

        if hasattr(pkg, "determine_variants"):
            find_attributes.append("variants")

        # If the package does not define 'determine_version' nor
        # 'determine_variants', then it must use some custom detection
        # mechanism. In this case, just inform the user it's detectable somehow.
        color.cprint(
            "    True{0}".format(
                " (" + ", ".join(find_attributes) + ")" if find_attributes else ""
            )
        )
    else:
        color.cprint("    False")


def print_maintainers(pkg):
    """output package maintainers"""

    if len(pkg.maintainers) > 0:
        mnt = " ".join(["@@" + m for m in pkg.maintainers])
        color.cprint("")
        color.cprint(section_title("Maintainers: ") + mnt)


def print_phases(pkg):
    """output installation phases"""

    if hasattr(pkg.builder, "phases") and pkg.builder.phases:
        color.cprint("")
        color.cprint(section_title("Installation Phases:"))
        phase_str = ""
        for phase in pkg.builder.phases:
            phase_str += "    {0}".format(phase)
        color.cprint(phase_str)


def print_tags(pkg):
    """output package tags"""

    color.cprint("")
    color.cprint(section_title("Tags: "))
    if hasattr(pkg, "tags"):
        tags = sorted(pkg.tags)
        colify(tags, indent=4)
    else:
        color.cprint("    None")


def print_tests(pkg):
    """output relevant build-time and stand-alone tests"""

    # Some built-in base packages (e.g., Autotools) define callback (e.g.,
    # check) inherited by descendant packages. These checks may not result
    # in build-time testing if the package's build does not implement the
    # expected functionality (e.g., a 'check' or 'test' targets).
    #
    # So the presence of a callback in Spack does not necessarily correspond
    # to the actual presence of built-time tests for a package.
    for callbacks, phase in [
        (getattr(pkg, "build_time_test_callbacks", None), "Build"),
        (getattr(pkg, "install_time_test_callbacks", None), "Install"),
    ]:
        color.cprint("")
        color.cprint(section_title("Available {0} Phase Test Methods:".format(phase)))
        names = []
        if callbacks:
            for name in callbacks:
                if getattr(pkg, name, False):
                    names.append(name)

        if names:
            colify(sorted(names), indent=4)
        else:
            color.cprint("    None")

    # PackageBase defines an empty install/smoke test but we want to know
    # if it has been overridden and, therefore, assumed to be implemented.
    color.cprint("")
    color.cprint(section_title("Stand-Alone/Smoke Test Methods:"))
    names = spack.install_test.test_function_names(pkg, add_virtuals=True)
    if names:
        colify(sorted(names), indent=4)
    else:
        color.cprint("    None")


def _fmt_value(v):
    if v is None or isinstance(v, bool):
        return str(v).lower()
    else:
        return str(v)


def _fmt_name_and_default(variant):
    """Print colorized name [default] for a variant."""
    return color.colorize(f"@c{{{variant.name}}} @C{{[{_fmt_value(variant.default)}]}}")


def _fmt_when(variant, indent):
    pass


def _fmt_variant_description(variant, width, indent):
    """Format a variant's description, preserving explicit line breaks."""
    return "\n".join(
        textwrap.fill(
            line, width=width, initial_indent=indent * " ", subsequent_indent=indent * " "
        )
        for line in variant.description.split("\n")
    )


def _fmt_variant(variant, when, max_name_default_len, indent, out=None):
    out = out or sys.stdout

    _, cols = tty.terminal_size()

    name_and_default = _fmt_name_and_default(variant)
    name_default_len = color.clen(name_and_default)

    values = variant.values
    if not isinstance(variant.values, (tuple, list, spack.variant.DisjointSetsOfValues)):
        values = [variant.values]
        print(type(values), values)

    # put 'none' first, sort the rest by value
    sorted_values = sorted(values, key=lambda v: (v != "none", v))

    pad = 4  # min padding between 'name [default]' and values
    value_indent = (indent + max_name_default_len + pad) * " "  # left edge of values

    formatted_values = "\n".join(
        textwrap.wrap(
            f"{', '.join(_fmt_value(v) for v in sorted_values)}",
            width=cols - 2,
            initial_indent=value_indent,
            subsequent_indent=value_indent,
        )
    )
    formatted_values = formatted_values[indent + name_default_len + pad :]

    # name [default]   value1, value2, value3, ...
    padding = pad * " "
    color.cprint(f"{indent * ' '}{name_and_default}{padding}@c{{{formatted_values}}}", stream=out)

    # when <spec>
    if when != spack.spec.Spec():
        color.cprint(f"{(indent*2) * ' '}@B{{when}} {color.cescape(when)}", stream=out)

    # description, preserving explicit line breaks from the way it's written in the package file
    out.write(_fmt_variant_description(variant, cols - 2, indent * 3))
    out.write("\n")


def print_variants(pkg):
    """output variants"""

    if not pkg.variants:
        print("    None")
        return

    color.cprint("")
    color.cprint(section_title("Variants:"))

    variants_by_name = pkg.variants_by_name(when=True)

    # calculate the max length of the "name [default]" part of the variant display
    max_name_default_len = max(
        color.clen(_fmt_name_and_default(variant))
        for name, when_variants in variants_by_name.items()
        for variants in when_variants.values()
        for variant in variants
    )

    conditionals = {}

    indent = 4
    for name, when_variants in variants_by_name.items():
        from pprint import pprint

        pprint(when_variants)

        if len(when_variants) > 1:
            print("    ", len(when_variants), "variants")
            all_variants = sum(when_variants.values(), start=[])
            if not all(v == all_variants[0] for v in all_variants):
                color.cprint(f"@r{{-->}} @r{{{name}}} (different)")
            else:
                color.cprint(f"@g{{-->}} @g{{{name}}} (same)")

            for when, variants in when_variants.items():
                for variant in variants:
                    _fmt_variant(variant, when, max_name_default_len, indent, out=sys.stdout)
            continue

        when, variants = next(iter(when_variants.items()))
        _fmt_variant(variants[0], when, max_name_default_len, indent, out=sys.stdout)


def print_versions(pkg):
    """output versions"""

    color.cprint("")
    color.cprint(section_title("Preferred version:  "))

    if not pkg.versions:
        color.cprint(version("    None"))
        color.cprint("")
        color.cprint(section_title("Safe versions:  "))
        color.cprint(version("    None"))
        color.cprint("")
        color.cprint(section_title("Deprecated versions:  "))
        color.cprint(version("    None"))
    else:
        pad = padder(pkg.versions, 4)

        preferred = preferred_version(pkg)
        url = ""
        if pkg.has_code:
            url = fs.for_package_version(pkg, preferred)

        line = version("    {0}".format(pad(preferred))) + color.cescape(url)
        color.cprint(line)

        safe = []
        deprecated = []
        for v in reversed(sorted(pkg.versions)):
            if pkg.has_code:
                url = fs.for_package_version(pkg, v)
            if pkg.versions[v].get("deprecated", False):
                deprecated.append((v, url))
            else:
                safe.append((v, url))

        for title, vers in [("Safe", safe), ("Deprecated", deprecated)]:
            color.cprint("")
            color.cprint(section_title("{0} versions:  ".format(title)))
            if not vers:
                color.cprint(version("    None"))
                continue

            for v, url in vers:
                line = version("    {0}".format(pad(v))) + color.cescape(url)
                color.cprint(line)


def print_virtuals(pkg):
    """output virtual packages"""

    color.cprint("")
    color.cprint(section_title("Virtual Packages: "))
    if pkg.provided:
        for when, specs in reversed(sorted(pkg.provided.items())):
            line = "    %s provides %s" % (
                when.colorized(),
                ", ".join(s.colorized() for s in specs),
            )
            print(line)

    else:
        color.cprint("    None")


def info(parser, args):
    spec = spack.spec.Spec(args.package)
    pkg_cls = spack.repo.PATH.get_pkg_class(spec.name)
    pkg = pkg_cls(spec)

    # Output core package information
    header = section_title("{0}:   ").format(pkg.build_system_class) + pkg.name
    color.cprint(header)

    color.cprint("")
    color.cprint(section_title("Description:"))
    if pkg.__doc__:
        color.cprint(color.cescape(pkg.format_doc(indent=4)))
    else:
        color.cprint("    None")

    color.cprint(section_title("Homepage: ") + pkg.homepage)

    # Now output optional information in expected order
    sections = [
        (args.all or args.maintainers, print_maintainers),
        (args.all or args.detectable, print_detectable),
        (args.all or args.tags, print_tags),
        (args.all or not args.no_versions, print_versions),
        (args.all or not args.no_variants, print_variants),
        (args.all or args.phases, print_phases),
        (args.all or not args.no_dependencies, print_dependencies),
        (args.all or args.virtuals, print_virtuals),
        (args.all or args.tests, print_tests),
    ]
    for print_it, func in sections:
        if print_it:
            func(pkg)

    color.cprint("")

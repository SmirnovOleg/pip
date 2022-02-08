from functools import partial
from optparse import Values
from typing import List, Optional

from pip._internal.cache import WheelCache
from pip._internal.cli.cmdoptions import make_target_python
from pip._internal.cli.req_command import SessionCommandMixin
from pip._internal.cli.status_codes import SUCCESS
from pip._internal.commands.install import InstallCommand
from pip._internal.exceptions import CommandError
from pip._internal.index.collector import LinkCollector
from pip._internal.index.package_finder import PackageFinder
# from pip._internal.models.index import PyPI
from pip._internal.models.selection_prefs import SelectionPreferences
from pip._internal.models.target_python import TargetPython
from pip._internal.network.session import PipSession
from pip._internal.operations.prepare import RequirementPreparer
from pip._internal.req import parse_requirements, InstallRequirement, RequirementSet
from pip._internal.req.constructors import install_req_from_req_string, install_req_from_line, \
    install_req_from_parsed_requirement, install_req_from_editable
from pip._internal.req.req_tracker import get_requirement_tracker, RequirementTracker
from pip._internal.utils.temp_dir import TempDirectory


class ResolveCommand(InstallCommand, SessionCommandMixin):

    def run(self, options: Values, args: List[str]) -> int:
        if not args:
            raise CommandError("Missing required argument (resolve query).")
        self.resolve(options, args)
        return SUCCESS

    def resolve(self, options: Values, args: List[str]):
        upgrade_strategy = "to-satisfy-only"
        if options.upgrade:
            upgrade_strategy = options.upgrade_strategy

        session = self.get_default_session(options)
        target_python = make_target_python(options)
        finder = self._build_package_finder(
            options=options,
            session=session,
            target_python=target_python,
            ignore_requires_python=options.ignore_requires_python,
        )

        wheel_cache = WheelCache(options.cache_dir, options.format_control)
        req_tracker = self.enter_context(get_requirement_tracker())
        directory = TempDirectory(
            delete=not options.no_clean,
            kind="install",
            globally_managed=True,
        )

        preparer = self.make_requirement_preparer(
            temp_build_dir=directory,
            options=options,
            req_tracker=req_tracker,
            session=session,
            finder=finder,
            use_user_site=options.use_user_site,
        )

        resolver = self.make_resolver(
            preparer=preparer,
            finder=finder,
            options=options,
            wheel_cache=wheel_cache,
            use_user_site=options.use_user_site,
            ignore_installed=options.ignore_installed,
            ignore_requires_python=options.ignore_requires_python,
            force_reinstall=options.force_reinstall,
            upgrade_strategy=upgrade_strategy,
            use_pep517=options.use_pep517,
        )

        reqs = self.get_requirements(args, options, finder, session)
        requirement_set = resolver.resolve(
            reqs, check_supported_wheels=not options.target_dir
        )

        self._output(requirement_set)

    @staticmethod
    def _output(requirement_set: RequirementSet):
        print('--- RESOLVED-BEGIN ---')
        for req in requirement_set.all_requirements:
            print(f'name: {req.name}')
            print(f'specifier: {req.specifier}')
            print(f'link.ext: {req.link.ext}')
            print(f'link.filename: {req.link.filename}')
            print(f'link.comes_from: {req.link.comes_from}')
            print(f'link.url: {req.link.url}')
            print(f'comes_from.link.url: {req.comes_from.link.url if req.comes_from else None}')
        print('--- RESOLVED-END ---')

    def get_requirements(
            self,
            args: List[str],
            options: Values,
            finder: PackageFinder,
            session: PipSession,
    ) -> List[InstallRequirement]:
        """
        Parse command-line arguments into the corresponding requirements.
        """
        requirements: List[InstallRequirement] = []
        for filename in options.constraints:
            for parsed_req in parse_requirements(
                    filename,
                    constraint=True,
                    finder=finder,
                    options=options,
                    session=session,
            ):
                req_to_add = install_req_from_parsed_requirement(
                    parsed_req,
                    isolated=options.isolated_mode,
                    user_supplied=False,
                )
                requirements.append(req_to_add)

        for req in args:
            req_to_add = install_req_from_line(
                req,
                None,
                isolated=options.isolated_mode,
                use_pep517=options.use_pep517,
                user_supplied=True,
            )
            requirements.append(req_to_add)

        for req in options.editables:
            req_to_add = install_req_from_editable(
                req,
                user_supplied=True,
                isolated=options.isolated_mode,
                use_pep517=options.use_pep517,
            )
            requirements.append(req_to_add)

        # NOTE: options.require_hashes may be set if --require-hashes is True
        for filename in options.requirements:
            for parsed_req in parse_requirements(
                    filename, finder=finder, options=options, session=session
            ):
                req_to_add = install_req_from_parsed_requirement(
                    parsed_req,
                    isolated=options.isolated_mode,
                    use_pep517=options.use_pep517,
                    user_supplied=True,
                )
                requirements.append(req_to_add)

        # If any requirement has hash options, enable hash checking.
        if any(req.has_hash_options for req in requirements):
            options.require_hashes = True

        if not (args or options.editables or options.requirements):
            opts = {"name": self.name}
            if options.find_links:
                raise CommandError(
                    "You must give at least one requirement to {name} "
                    '(maybe you meant "pip {name} {links}"?)'.format(
                        **dict(opts, links=" ".join(options.find_links))
                    )
                )
            else:
                raise CommandError(
                    "You must give at least one requirement to {name} "
                    '(see "pip help {name}")'.format(**opts)
                )

        return requirements

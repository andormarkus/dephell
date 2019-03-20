# built-in
from email.parser import Parser
from itertools import chain
from pathlib import Path

# external
from packaging.requirements import Requirement as PackagingRequirement

# app
from ..archive import ArchivePath
from ..controllers import DependencyMaker, Readme
from ..models import Author, RootDependency
from .base import BaseConverter


class EggInfoConverter(BaseConverter):
    """
    PEP-314, PEP-345, PEP-566
    https://packaging.python.org/specifications/core-metadata/
    """
    lock = False

    def load(self, path) -> RootDependency:
        path = Path(str(path))
        if path.is_dir():
            # load from *.egg-info dir
            if path.name.endswith('.egg-info'):
                return self._load_dir(path)
            # find *.egg-info in current dir
            paths = list(path.glob('**/*.egg-info'))
            return self._load_dir(*paths)

        # load from archive
        if path.suffix in ('.zip', '.gz', '.tar'):
            archive = ArchivePath(path)
            paths = list(archive.glob('**/*.egg-info'))
            root = self._load_dir(*paths)
            root.readme = Readme.discover(path=archive)
            return root

        # load from file (requires.txt or PKG-INFO)
        with path.open('r') as stream:
            return self.loads(stream.read())

    def loads(self, content: str) -> RootDependency:
        if 'Name: ' in content:
            return self._parse_info(content)
        else:
            return self._parse_requires(content)

    def dumps(self, reqs, project: RootDependency, content=None) -> str:
        # distutils.dist.DistributionMetadata.write_pkg_file
        content = []
        content.append(('Metadata-Version', '2.1'))
        content.append(('Name', project.raw_name))
        content.append(('Version', project.version))
        if project.description:
            content.append(('Summary', project.description))

        # links
        fields = (
            ('home', 'Home-Page'),
            ('download', 'Download-URL'),
            ('project', 'Project-URL'),
        )
        for key, name in fields:
            if key in project.links:
                content.append((name, project.links[key]))

        # authors
        if project.authors:
            author = project.authors[0]
            content.append(('Author', author.name))
            if author.mail:
                content.append(('Author-email', author.mail))
        if len(project.authors) > 1:
            author = project.authors[1]
            content.append(('Maintainer', author.name))
            if author.mail:
                content.append(('Maintainer-email', author.mail))

        if project.license:
            content.append(('License', project.license))
        if project.keywords:
            content.append(('Keywords', ','.join(project.keywords)))
        for classifier in project.classifiers:
            content.append(('Classifier', classifier))
        for platform in project.platforms:
            content.append(('Platform', platform))
        for req in reqs:
            content.append(('Requires', self._format_req(req=req)))

        content = '\n'.join(map(': '.join, content))
        if project.readme:
            content += '\n\n' + project.readme.as_rst()
        return content

    # helpers

    def _load_dir(self, *paths):
        if not paths:
            raise FileNotFoundError('cannot find egg-info')
        # maybe it's possible, so we will have to process it
        if len(paths) > 1:
            raise FileExistsError('too many egg-info')
        path = paths[0]

        with (path / 'PKG-INFO').open('r') as stream:
            content = stream.read()
        root = self._parse_info(content)
        path = path / 'requires.txt'
        if not root.dependencies:
            with path.open('r') as stream:
                content = stream.read()
            root = self._parse_requires(content, root=root)
        return root

    @classmethod
    def _parse_info(cls, content: str, root=None) -> RootDependency:
        info = Parser().parsestr(content)
        root = RootDependency(
            raw_name=cls._get(info, 'Name'),
            version=cls._get(info, 'Version') or '0.0.0',

            description=cls._get(info, 'Summary'),
            license=cls._get(info, 'License'),

            keywords=cls._get(info, 'Keywords').split(','),
            classifiers=cls._get_list(info, 'Classifier'),
            platforms=cls._get_list(info, 'Platform'),
        )

        # links
        fields = (
            ('home', 'Home-Page'),
            ('download', 'Download-URL'),
            ('project', 'Project-URL'),
        )
        for key, name in fields:
            link = cls._get(info, name)
            if link:
                root.links[key] = link

        # authors
        for name in ('author', 'maintainer'):
            author = cls._get(info, name)
            if author:
                root.authors += (
                    Author(name=author, mail=cls._get(info, name + '_email')),
                )

        # dependencies
        deps = []
        reqs = chain(
            cls._get_list(info, 'Requires'),
            cls._get_list(info, 'Requires-Dist'),
        )
        for req in reqs:
            req = PackagingRequirement(req)
            deps.extend(DependencyMaker.from_requirement(source=root, req=req))
        root.attach_dependencies(deps)
        return root

    def _parse_requires(self, content: str, root=None) -> RootDependency:
        if root is None:
            root = RootDependency(raw_name=self._get_name(content=content))
        deps = []
        for req in content.split():
            req = PackagingRequirement(req)
            deps.extend(DependencyMaker.from_requirement(source=root, req=req))
        root.attach_dependencies(deps)
        return root

    @staticmethod
    def _format_req(req):
        line = req.name
        if req.extras:
            line += '[{extras}]'.format(extras=','.join(req.extras))
        if req.version:
            line += req.version
        if req.markers:
            line += '; ' + req.markers
        return line

    @staticmethod
    def _get(msg, name: str) -> str:
        value = msg.get(name)
        if not value:
            return ''
        value = value.strip()
        if value == 'UNKNOWN':
            return ''
        return value

    @staticmethod
    def _get_list(msg, name: str) -> tuple:
        values = msg.get_all(name)
        if not values:
            return ()
        return tuple(value.strip() for value in values if value.strip() != 'UNKNOWN')
import glob
from doit.action import CmdAction


PACKAGE = "efb_fb_messenger_slave"
README_BASE = "./README.rst"
DEFAULT_BUMP_MODE = "beta"
# major, minor, patch, alpha, beta, dev, post
DOIT_CONFIG = {
    "default_tasks": ["msgfmt"]
}


def task_gettext():
    pot = f"./{PACKAGE}/locale/{PACKAGE}.pot"
    sources = glob.glob(f"./{PACKAGE}/**/*.py", recursive=True)
    sources = [i for i in sources if "__version__.py" not in i]
    command = "xgettext --add-comments=TRANSLATORS -o " + pot + " " + " ".join(sources)
    sources.append(README_BASE)
    return {
        "actions": [
            command,
            ['cp', README_BASE, './.cache/README.rst'],
            ['sphinx-build', '-b', 'gettext', '-C', '-D', 'master_doc=README',
             '-D', 'gettext_additional_targets=literal-block,image',
             './.cache', './readme_translations/locale/', './.cache/README.rst'],
            ['rm', './.cache/README.rst'],
        ],
        "targets": [
            pot,
            "./readme_translations/locale/README.pot"
        ],
        "file_dep": sources
    }


def task_msgfmt():
    languages = [i[i.rfind('/')+1:i.rfind('.')] for i in glob.glob("./readme_translations/locale/*.po")]

    sources = glob.glob("./**/*.po", recursive=True)
    dests = [i[:-3] + ".mo" for i in sources]
    actions = [["msgfmt", sources[i], "-o", dests[i]] for i in range(len(sources))]

    actions.append(["mkdir", "./.cache/source"])
    actions.append(["cp", README_BASE, "./.cache/source/README.rst"])
    for i in languages:
        actions.append(["sphinx-build", "-E", "-b", "rst", "-C",
                        "-D", f"language={i}", "-D", "locale_dirs=./readme_translations/locale",
                        "-D", "extensions=sphinxcontrib.restbuilder",
                        "-D", "master_doc=README", "./.cache/source", f"./.cache/{i}"])
        actions.append(["mv", f"./.cache/{i}/README.rst", f"./readme_translations/{i}.rst"])
        actions.append(["rm", "-rf", f"./.cache/{i}"])
    actions.append(["rm", "-rf", "./.cache/source"])

    return {
        "actions": actions,
        "targets": dests,
        "file_dep": sources,
        "task_dep": ['crowdin', 'crowdin_pull']
    }


def task_crowdin():
    sources = glob.glob("./{package}/**/*.po".format(package=PACKAGE), recursive=True)
    return {
        "actions": ["crowdin upload sources"],
        "file_dep": sources,
        "task_dep": ["gettext"]
    }


def task_crowdin_pull():
    return {
        "actions": ["crowdin download"]
    }


def task_commit_lang_file():
    return {
        "actions": [
            ("git", "add", "*.po"),
            ("git", "commit", "-s", "Sync localization files from Crowdin")
        ],
        "task_dep": ["crowdin", "crowdin_pull"]
    }


def task_bump_version():
    def gen_bump_version(mode=DEFAULT_BUMP_MODE):
        return './bump.py ' + mode

    return {
        "actions": [CmdAction(gen_bump_version)],
        "params": [
            {
                "name": "Version bump mode",
                "short": "b",
                "long": "bump",
                "default": DEFAULT_BUMP_MODE,
                "help": "{major}.{minor}.{patch}{(a|b)}{.post}{.dev}",
                "choices": [
                    ("major", "Bump a major version"),
                    ("minor", "Bump a minor version"),
                    ("patch", "Bump a patch version"),
                    ("alpha", "Bump to the next alpha version"),
                    ("alpha", "Bump to the next beta version"),
                    ("post", "Bump to the next post version"),
                    ("dev", "Bump a dev version (for commit only)")
                ]
            }
        ]
    }


def task_mypy():
    sources = glob.glob("./{package}/**/*.py".format(package=PACKAGE), recursive=True)
    actions = ["mypy {}".format(i) for i in sources]
    return {
        "actions": actions
    }


def task_test():
    return {
        "actions": [
            "coverage run --source ./{} pytest".format(PACKAGE),
            "coverage report"
        ]
    }


def task_build():
    return {
        "actions": [
            "python setup.py sdist bdist_wheel"
        ]
    }


def task_publish():
    def get_twine_command():
        __version__ = __import__("{}.__version__".format(PACKAGE)).__version__
        if 'dev' in __version__:
            raise ValueError(f"Cannot publish dev version ({__version__}).")
        binarys = glob.glob("./dist/*{}*".format(__version__), recursive=True)
        return ["twine", "upload"] + binarys
    return {
        "actions": [get_twine_command],
        "task_dep": ["test", "msgfmt", "build"]
    }

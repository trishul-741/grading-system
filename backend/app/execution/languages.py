from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from app.enums import ProgrammingLanguage


@dataclass(frozen=True)
class LanguageConfig:
    image: str
    source_file: str
    compile_command: tuple[str, ...]
    run_command: tuple[str, ...]


# Only reviewed, implemented languages belong in this fixed mapping. These are
# arguments to the image's trusted entrypoint, never interpolated shell text.
LANGUAGES: Mapping[ProgrammingLanguage, LanguageConfig] = MappingProxyType({
    ProgrammingLanguage.CPP: LanguageConfig(
        image="grading-cpp:1",
        source_file="main.cpp",
        compile_command=("compile",),
        run_command=("run",),
    ),
})


class UnsupportedLanguageError(ValueError):
    """The language enum has no implemented and tested execution adapter."""


def get_language_config(language: ProgrammingLanguage) -> LanguageConfig:
    try:
        return LANGUAGES[language]
    except (KeyError, TypeError) as exc:
        raise UnsupportedLanguageError("This programming language is not supported") from exc

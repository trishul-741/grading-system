from dataclasses import dataclass


@dataclass
class LanguageConfig:

    image: str

    source_file: str

    compile_command: str | None

    run_command: str


LANGUAGES = {

    "CPP": LanguageConfig(
        image="grading-cpp:latest",
        source_file="main.cpp",
        compile_command=(
            "g++ -std=c++17 -O2 "
            "-pipe main.cpp -o main"
        ),
        run_command="./main"
    )

}
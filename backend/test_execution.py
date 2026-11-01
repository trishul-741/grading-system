from app.execution.sandbox import execute_cpp


code = """
#include <iostream>
using namespace std;

int main() {

    int a, b;

    cin >> a >> b;

    cout << a + b;

    return 0;
}
"""

# code = """
# #include <iostream>

# int main() {

#     this_is_invalid;

#     return 0;
# }
# """


if __name__ == "__main__":
    result = execute_cpp(source_code=code, input_data="10 20")
    print(result.model_dump_json())

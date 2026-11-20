from app.services.breaking_detector import detect_in_file, extract_js, extract_python


def kinds(findings):
    return {(f["symbol"], f["kind"]) for f in findings}


def test_python_removed_and_renamed():
    old = "def a(x): pass\ndef b(x, y): pass\n"
    new = "def a2(x): pass\n"
    k = kinds(detect_in_file("m.py", old, new))
    assert ("a", "renamed") in k and ("b", "removed") in k


def test_python_param_changes():
    old = "def f(a, b=1): pass\ndef g(a): pass\ndef h(a, b): pass\n"
    new = "def f(a, b): pass\ndef g(a, c): pass\ndef h(b, a): pass\n"
    k = kinds(detect_in_file("m.py", old, new))
    assert ("f", "param_now_required") in k
    assert ("g", "param_added_required") in k
    assert ("h", "param_reordered") in k


def test_python_safe_changes_not_flagged():
    old = "def f(a): pass\n"
    new = "def f(a, b=2): pass\ndef extra(): pass\ndef _private(): pass\n"
    assert detect_in_file("m.py", old, new) == []


def test_python_methods_and_private_ignored():
    old = "class A:\n    def run(self, x): pass\n    def _hidden(self, x): pass\n"
    new = "class A:\n    def run(self): pass\n    def _hidden(self): pass\n"
    assert kinds(detect_in_file("m.py", old, new)) == {("A.run", "param_removed")}


def test_js_extraction():
    src = """
export function login(user, pass = '', ...rest) {}
export const add = async (a: number, b?: number): Promise<number> => a + b;
export const notFn = (1 + 2);
function internal(a) {}
module.exports.legacy = function (x) {};
"""
    sigs = extract_js(src)
    assert set(sigs) == {"login", "add", "legacy"}
    assert sigs["add"].params[1].has_default is True


def test_js_breaking():
    old = "export function login(user, pass) {}\nexport const f = (a) => a;\n"
    new = "export function login(user) {}\n"
    k = kinds(detect_in_file("a.ts", old, new))
    assert ("login", "param_removed") in k and ("f", "removed") in k


def test_removed_file_flags_everything():
    assert kinds(detect_in_file("m.py", "def a(): pass\n", None)) == {("a", "removed")}

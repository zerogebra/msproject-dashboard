import os
import jpype

_JRE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "jre", "jdk-11.0.23+9-jre"
)
_JVM_DLL = os.path.join(_JRE_ROOT, "bin", "client", "jvm.dll")


def _ensure_jvm():
    if not jpype.isJVMStarted():
        # importing mpxj registers all its JARs via jpype.addClassPath()
        import mpxj  # noqa: F401
        # Start JVM using our portable JRE; classpath is already set by mpxj import
        jpype.startJVM(_JVM_DLL, convertStrings=True)


class MPPReader:
    def __init__(self, jar: str = ""):
        self._reader_cls = None

    def load(self, path: str):
        _ensure_jvm()
        import jpype.imports  # noqa: F401
        from jpype import JClass

        if self._reader_cls is None:
            self._reader_cls = JClass("org.mpxj.reader.UniversalProjectReader")

        reader = self._reader_cls()
        return reader.read(path)

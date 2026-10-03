/* splitroll.exe — tiny GUI launcher that hosts the bundled Python runtime.
 *
 * Layout next to this exe:
 *   runtime\python312.dll (+ Lib, DLLs, python312._pth)
 *   app\splitroll\...
 * The process image is splitroll.exe, so the taskbar, Task Manager and
 * Start menu all show the splitroll name and icon.
 */
#define WIN32_LEAN_AND_MEAN
#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif
#include <windows.h>
#include <shellapi.h>
#include <wchar.h>

typedef int (*Py_MainFn)(int, wchar_t **);

static void fail(const wchar_t *msg, const wchar_t *detail) {
    wchar_t buf[2048];
    _snwprintf(buf, 2047, L"%ls\n\n%ls\n\nTry reinstalling splitroll.", msg, detail ? detail : L"");
    buf[2047] = 0;
    MessageBoxW(NULL, buf, L"splitroll", MB_ICONERROR | MB_OK);
}

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmd, int show) {
    (void)hInst; (void)hPrev; (void)cmd; (void)show;
    wchar_t exe[MAX_PATH * 4], dir[MAX_PATH * 4], rt[MAX_PATH * 4], dll[MAX_PATH * 4];
    DWORD n = GetModuleFileNameW(NULL, exe, MAX_PATH * 4);
    if (!n || n >= MAX_PATH * 4) { fail(L"Could not locate splitroll.", NULL); return 1; }
    wcscpy(dir, exe);
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;
    _snwprintf(rt, MAX_PATH * 4, L"%ls\\runtime", dir);
    _snwprintf(dll, MAX_PATH * 4, L"%ls\\python312.dll", rt);

    /* python3.dll / vcruntime live in runtime\, not next to this exe: make them findable
       for the runtime itself and for every extension module Python loads later */
    SetDllDirectoryW(rt);
    AddDllDirectory(rt);
    HMODULE py = LoadLibraryExW(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!py) { fail(L"The bundled Python runtime is missing or damaged.", dll); return 1; }
    Py_MainFn py_main = (Py_MainFn)GetProcAddress(py, "Py_Main");
    if (!py_main) { fail(L"The bundled Python runtime is incompatible.", dll); return 1; }

    /* forward any command-line arguments after "-m splitroll" */
    int argc = 0;
    wchar_t **argv_in = CommandLineToArgvW(GetCommandLineW(), &argc);
    int total = argc + 2;
    wchar_t **argv = (wchar_t **)HeapAlloc(GetProcessHeap(), 0, sizeof(wchar_t *) * (total + 1));
    argv[0] = exe;
    argv[1] = L"-m";
    argv[2] = L"splitroll";
    for (int i = 1; i < argc; i++) argv[i + 2] = argv_in[i];
    argv[total] = NULL;
    return py_main(total, argv);
}

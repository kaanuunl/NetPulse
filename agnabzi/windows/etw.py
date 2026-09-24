"""Real-time ETW consumer for the Microsoft-Windows-Kernel-Network provider.

This is the same data source Resource Monitor uses to show network activity per
process. Opening a real-time session requires administrator rights.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import uuid
from ctypes import wintypes

from agnabzi.traffic import KERNEL_NETWORK_EVENTS, FlowAccumulator, TrafficSample, parse_kernel_network

log = logging.getLogger(__name__)

SESSION_NAME = "NetPulse-KernelNetwork"
KERNEL_NETWORK_PROVIDER = uuid.UUID("7dd42a49-5329-4832-8dfd-43d979153a88")

WNODE_FLAG_TRACED_GUID = 0x00020000
EVENT_TRACE_REAL_TIME_MODE = 0x00000100
EVENT_TRACE_CONTROL_STOP = 1
EVENT_CONTROL_CODE_ENABLE_PROVIDER = 1
TRACE_LEVEL_VERBOSE = 5
PROCESS_TRACE_MODE_REAL_TIME = 0x00000100
PROCESS_TRACE_MODE_EVENT_RECORD = 0x10000000
ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183
INVALID_PROCESSTRACE_HANDLE = {0xFFFFFFFFFFFFFFFF, 0x00000000FFFFFFFF}

TRACEHANDLE = ctypes.c_uint64
ULONG = wintypes.ULONG


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> GUID:
        return cls.from_buffer_copy(value.bytes_le)


class WNODE_HEADER(ctypes.Structure):
    _fields_ = [
        ("BufferSize", ULONG),
        ("ProviderId", ULONG),
        ("HistoricalContext", ctypes.c_uint64),
        ("TimeStamp", ctypes.c_int64),
        ("Guid", GUID),
        ("ClientContext", ULONG),
        ("Flags", ULONG),
    ]


class EVENT_TRACE_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("Wnode", WNODE_HEADER),
        ("BufferSize", ULONG),
        ("MinimumBuffers", ULONG),
        ("MaximumBuffers", ULONG),
        ("MaximumFileSize", ULONG),
        ("LogFileMode", ULONG),
        ("FlushTimer", ULONG),
        ("EnableFlags", ULONG),
        ("AgeLimit", wintypes.LONG),
        ("NumberOfBuffers", ULONG),
        ("FreeBuffers", ULONG),
        ("EventsLost", ULONG),
        ("BuffersWritten", ULONG),
        ("LogBuffersLost", ULONG),
        ("RealTimeBuffersLost", ULONG),
        ("LoggerThreadId", wintypes.HANDLE),
        ("LogFileNameOffset", ULONG),
        ("LoggerNameOffset", ULONG),
    ]


class _SessionProperties(ctypes.Structure):
    _fields_ = [
        ("properties", EVENT_TRACE_PROPERTIES),
        ("logger_name", ctypes.c_wchar * 1024),
        ("log_file_name", ctypes.c_wchar * 1024),
    ]

    @classmethod
    def create(cls, for_control: bool = False) -> _SessionProperties:
        block = cls()
        props = block.properties
        props.Wnode.BufferSize = ctypes.sizeof(cls)
        props.Wnode.Flags = WNODE_FLAG_TRACED_GUID
        props.Wnode.ClientContext = 1
        props.Wnode.Guid = GUID.from_uuid(uuid.uuid4())
        props.BufferSize = 64
        props.MinimumBuffers = 4
        props.MaximumBuffers = 64
        props.FlushTimer = 1
        props.LogFileMode = EVENT_TRACE_REAL_TIME_MODE
        props.LoggerNameOffset = cls.logger_name.offset
        props.LogFileNameOffset = cls.log_file_name.offset if for_control else 0
        return block


class EVENT_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Id", wintypes.USHORT),
        ("Version", ctypes.c_ubyte),
        ("Channel", ctypes.c_ubyte),
        ("Level", ctypes.c_ubyte),
        ("Opcode", ctypes.c_ubyte),
        ("Task", wintypes.USHORT),
        ("Keyword", ctypes.c_uint64),
    ]


class EVENT_HEADER(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.USHORT),
        ("HeaderType", wintypes.USHORT),
        ("Flags", wintypes.USHORT),
        ("EventProperty", wintypes.USHORT),
        ("ThreadId", ULONG),
        ("ProcessId", ULONG),
        ("TimeStamp", ctypes.c_int64),
        ("ProviderId", GUID),
        ("EventDescriptor", EVENT_DESCRIPTOR),
        ("ProcessorTime", ctypes.c_uint64),
        ("ActivityId", GUID),
    ]


class ETW_BUFFER_CONTEXT(ctypes.Structure):
    _fields_ = [("ProcessorIndex", wintypes.USHORT), ("LoggerId", wintypes.USHORT)]


class EVENT_RECORD(ctypes.Structure):
    _fields_ = [
        ("EventHeader", EVENT_HEADER),
        ("BufferContext", ETW_BUFFER_CONTEXT),
        ("ExtendedDataCount", wintypes.USHORT),
        ("UserDataLength", wintypes.USHORT),
        ("ExtendedData", ctypes.c_void_p),
        ("UserData", ctypes.c_void_p),
        ("UserContext", ctypes.c_void_p),
    ]


class EVENT_TRACE_HEADER(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.USHORT),
        ("FieldTypeFlags", wintypes.USHORT),
        ("Version", ULONG),
        ("ThreadId", ULONG),
        ("ProcessId", ULONG),
        ("TimeStamp", ctypes.c_int64),
        ("Guid", GUID),
        ("ProcessorTime", ctypes.c_uint64),
    ]


class EVENT_TRACE(ctypes.Structure):
    _fields_ = [
        ("Header", EVENT_TRACE_HEADER),
        ("InstanceId", ULONG),
        ("ParentInstanceId", ULONG),
        ("ParentGuid", GUID),
        ("MofData", ctypes.c_void_p),
        ("MofLength", ULONG),
        ("ClientContext", ULONG),
    ]


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        (name, wintypes.WORD)
        for name in ("wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")
    ]


class TIME_ZONE_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("Bias", wintypes.LONG),
        ("StandardName", wintypes.WCHAR * 32),
        ("StandardDate", SYSTEMTIME),
        ("StandardBias", wintypes.LONG),
        ("DaylightName", wintypes.WCHAR * 32),
        ("DaylightDate", SYSTEMTIME),
        ("DaylightBias", wintypes.LONG),
    ]


class TRACE_LOGFILE_HEADER(ctypes.Structure):
    _fields_ = [
        ("BufferSize", ULONG),
        ("Version", ULONG),
        ("ProviderVersion", ULONG),
        ("NumberOfProcessors", ULONG),
        ("EndTime", ctypes.c_int64),
        ("TimerResolution", ULONG),
        ("MaximumFileSize", ULONG),
        ("LogFileMode", ULONG),
        ("BuffersWritten", ULONG),
        ("LogInstanceGuid", GUID),
        ("LoggerName", ctypes.c_void_p),
        ("LogFileName", ctypes.c_void_p),
        ("TimeZone", TIME_ZONE_INFORMATION),
        ("BootTime", ctypes.c_int64),
        ("PerfFreq", ctypes.c_int64),
        ("StartTime", ctypes.c_int64),
        ("ReservedFlags", ULONG),
        ("BuffersLost", ULONG),
    ]


EVENT_RECORD_CALLBACK = ctypes.WINFUNCTYPE(None, ctypes.POINTER(EVENT_RECORD))


class EVENT_TRACE_LOGFILEW(ctypes.Structure):
    _fields_ = [
        ("LogFileName", wintypes.LPWSTR),
        ("LoggerName", wintypes.LPWSTR),
        ("CurrentTime", ctypes.c_int64),
        ("BuffersRead", ULONG),
        ("ProcessTraceMode", ULONG),
        ("CurrentEvent", EVENT_TRACE),
        ("LogfileHeader", TRACE_LOGFILE_HEADER),
        ("BufferCallback", ctypes.c_void_p),
        ("BufferSize", ULONG),
        ("Filled", ULONG),
        ("EventsLost", ULONG),
        ("EventRecordCallback", EVENT_RECORD_CALLBACK),
        ("IsKernelTrace", ULONG),
        ("Context", ctypes.c_void_p),
    ]


_advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)

_StartTraceW = _advapi32.StartTraceW
_StartTraceW.argtypes = [ctypes.POINTER(TRACEHANDLE), wintypes.LPCWSTR, ctypes.c_void_p]
_StartTraceW.restype = ULONG

_ControlTraceW = _advapi32.ControlTraceW
_ControlTraceW.argtypes = [TRACEHANDLE, wintypes.LPCWSTR, ctypes.c_void_p, ULONG]
_ControlTraceW.restype = ULONG

_EnableTraceEx2 = _advapi32.EnableTraceEx2
_EnableTraceEx2.argtypes = [
    TRACEHANDLE, ctypes.POINTER(GUID), ULONG, ctypes.c_ubyte,
    ctypes.c_uint64, ctypes.c_uint64, ULONG, ctypes.c_void_p,
]
_EnableTraceEx2.restype = ULONG

_OpenTraceW = _advapi32.OpenTraceW
_OpenTraceW.argtypes = [ctypes.POINTER(EVENT_TRACE_LOGFILEW)]
_OpenTraceW.restype = TRACEHANDLE

_ProcessTrace = _advapi32.ProcessTrace
_ProcessTrace.argtypes = [ctypes.POINTER(TRACEHANDLE), ULONG, ctypes.c_void_p, ctypes.c_void_p]
_ProcessTrace.restype = ULONG

_CloseTrace = _advapi32.CloseTrace
_CloseTrace.argtypes = [TRACEHANDLE]
_CloseTrace.restype = ULONG


class EtwError(OSError):
    def __init__(self, stage: str, code: int):
        super().__init__(code, f"{stage} failed with Win32 error {code}")
        self.stage = stage
        self.code = code

    @property
    def access_denied(self) -> bool:
        return self.code == ERROR_ACCESS_DENIED


def stop_session(name: str = SESSION_NAME) -> int:
    block = _SessionProperties.create(for_control=True)
    return _ControlTraceW(0, name, ctypes.byref(block), EVENT_TRACE_CONTROL_STOP)


class EtwTrafficSource:
    name = "etw"

    def __init__(self, session_name: str = SESSION_NAME):
        self._session_name = session_name
        self._flows = FlowAccumulator()
        self._session = TRACEHANDLE(0)
        self._trace = TRACEHANDLE(0)
        self._logfile: EVENT_TRACE_LOGFILEW | None = None
        self._thread: threading.Thread | None = None
        self._callback = EVENT_RECORD_CALLBACK(self._on_event)
        self._provider = GUID.from_uuid(KERNEL_NETWORK_PROVIDER)
        self._provider_data1 = self._provider.Data1
        self.events_seen = 0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        block = _SessionProperties.create()
        status = _StartTraceW(ctypes.byref(self._session), self._session_name, ctypes.byref(block))
        if status == ERROR_ALREADY_EXISTS:
            log.info("stopping a stale ETW session left behind by a previous run")
            stop_session(self._session_name)
            block = _SessionProperties.create()
            status = _StartTraceW(ctypes.byref(self._session), self._session_name, ctypes.byref(block))
        if status:
            raise EtwError("StartTrace", status)

        status = _EnableTraceEx2(
            self._session, ctypes.byref(self._provider), EVENT_CONTROL_CODE_ENABLE_PROVIDER,
            TRACE_LEVEL_VERBOSE, 0xFFFFFFFFFFFFFFFF, 0, 0, None,
        )
        if status:
            self._stop_session()
            raise EtwError("EnableTraceEx2", status)

        logfile = EVENT_TRACE_LOGFILEW()
        logfile.LoggerName = self._session_name
        logfile.ProcessTraceMode = PROCESS_TRACE_MODE_REAL_TIME | PROCESS_TRACE_MODE_EVENT_RECORD
        logfile.EventRecordCallback = self._callback
        handle = _OpenTraceW(ctypes.byref(logfile))
        if handle in INVALID_PROCESSTRACE_HANDLE:
            code = ctypes.get_last_error()
            self._stop_session()
            raise EtwError("OpenTrace", code)
        self._logfile = logfile
        self._trace = TRACEHANDLE(handle)
        self._thread = threading.Thread(target=self._pump, name="etw-consumer", daemon=True)
        self._thread.start()
        log.info("ETW session %s started", self._session_name)

    def _pump(self) -> None:
        status = _ProcessTrace(ctypes.byref(self._trace), 1, None, None)
        log.info("ETW consumer finished with status %s", status)

    def _on_event(self, record_pointer) -> None:
        try:
            record = record_pointer.contents
            header = record.EventHeader
            event_id = header.EventDescriptor.Id
            if event_id not in KERNEL_NETWORK_EVENTS or header.ProviderId.Data1 != self._provider_data1:
                return
            packet = parse_kernel_network(event_id, ctypes.string_at(record.UserData, record.UserDataLength))
            if packet is not None:
                self.events_seen += 1
                self._flows.add(packet.pid, packet.daddr, packet.saddr, packet.size, packet.sent)
        except Exception:  # exceptions cannot cross the ctypes callback boundary
            log.exception("failed to decode ETW event")

    def drain(self, local_addresses: set[bytes]) -> list[TrafficSample]:
        return self._flows.drain(local_addresses)

    def _stop_session(self) -> None:
        if self._session.value:
            block = _SessionProperties.create(for_control=True)
            _ControlTraceW(self._session, None, ctypes.byref(block), EVENT_TRACE_CONTROL_STOP)
            self._session = TRACEHANDLE(0)

    def stop(self) -> None:
        self._stop_session()
        if self._trace.value:
            _CloseTrace(self._trace)
            self._trace = TRACEHANDLE(0)
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None

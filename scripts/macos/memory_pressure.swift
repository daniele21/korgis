// Opt-in native signal adapter for Korgis. Build on macOS:
// swiftc -O scripts/macos/memory_pressure.swift -o /absolute/path/korgis-memory-pressure
//
// Run only via MacOSNativePressureEvents with stdin/stdout pipes. stdout is a
// minimal, path-free protocol; the process exits on parent stdin EOF/SIGKILL.
#if os(macOS)
import Dispatch
import Foundation

let queue = DispatchQueue(label: "korgis.native.memory-pressure")
let source = DispatchSource.makeMemoryPressureSource(
    eventMask: [.normal, .warning, .critical],
    queue: queue
)
source.setEventHandler {
    let event = source.data  // data, NOT mask (which is the subscribed filter)
    let label: String
    if event.contains(.critical) {
        label = "critical"
    } else if event.contains(.warning) {
        label = "warning"
    } else if event.contains(.normal) {
        label = "normal"
    } else {
        return
    }
    FileHandle.standardOutput.write(Data((label + "\n").utf8))
}
source.activate()
FileHandle.standardOutput.write(Data("ready\n".utf8))
// The owning Python process holds stdin open and closes it on shutdown.
// If that process exits unexpectedly, pipe EOF still terminates this child.
while readLine(strippingNewline: true) != nil {}
source.cancel()
#else
// The helper must not silently manufacture native events off macOS.
import Foundation
fputs("unsupported platform\n", stderr)
exit(2)
#endif

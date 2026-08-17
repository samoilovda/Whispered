import AVFoundation
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

private let protocolName = "whispered.system-audio"
private let protocolVersion = 1
private let magic = Data("WSCA".utf8)

enum HelperError: Error, CustomStringConvertible {
    case message(String)
    var description: String {
        switch self { case .message(let value): return value }
    }
}

private func frame(type: String, fields: [String: Any] = [:], payload: Data = Data()) throws -> Data {
    var header = fields
    header["protocol"] = protocolName
    header["version"] = protocolVersion
    header["type"] = type
    if type == "audio" { header["payload_bytes"] = payload.count }
    let json = try JSONSerialization.data(withJSONObject: header, options: [.sortedKeys])
    var output = Data()
    output.append(magic)
    var headerLength = UInt32(json.count).bigEndian
    var payloadLength = UInt32(payload.count).bigEndian
    withUnsafeBytes(of: &headerLength) { output.append(contentsOf: $0) }
    withUnsafeBytes(of: &payloadLength) { output.append(contentsOf: $0) }
    output.append(json)
    output.append(payload)
    return output
}

final class Connection {
    private let descriptor: Int32
    private let lock = NSLock()
    private var buffer = Data()

    init(descriptor: Int32) { self.descriptor = descriptor }
    deinit { Darwin.close(descriptor) }

    func send(type: String, fields: [String: Any] = [:], payload: Data = Data()) throws {
        let data = try frame(type: type, fields: fields, payload: payload)
        try lock.withLock {
            try data.withUnsafeBytes { raw in
                guard let base = raw.baseAddress else { return }
                var sent = 0
                while sent < data.count {
                    let count = Darwin.send(descriptor, base.advanced(by: sent), data.count - sent, 0)
                    if count <= 0 { throw HelperError.message("socket send failed: \(errno)") }
                    sent += count
                }
            }
        }
    }

    func nextControl() throws -> [String: Any] {
        while true {
            if buffer.count >= 12 {
                guard buffer.prefix(4) == magic else { throw HelperError.message("invalid IPC magic") }
                let headerLength = buffer.withUnsafeBytes { raw -> Int in
                    Int(UInt32(bigEndian: raw.loadUnaligned(fromByteOffset: 4, as: UInt32.self)))
                }
                let payloadLength = buffer.withUnsafeBytes { raw -> Int in
                    Int(UInt32(bigEndian: raw.loadUnaligned(fromByteOffset: 8, as: UInt32.self)))
                }
                let total = 12 + headerLength + payloadLength
                if buffer.count >= total {
                    let headerData = buffer.subdata(in: 12..<(12 + headerLength))
                    buffer.removeSubrange(0..<total)
                    guard payloadLength == 0,
                          let object = try JSONSerialization.jsonObject(with: headerData) as? [String: Any],
                          object["protocol"] as? String == protocolName,
                          object["version"] as? Int == protocolVersion else {
                        throw HelperError.message("invalid control frame")
                    }
                    return object
                }
            }
            var bytes = [UInt8](repeating: 0, count: 16 * 1024)
            let count = Darwin.recv(descriptor, &bytes, bytes.count, 0)
            if count <= 0 { throw HelperError.message("client disconnected") }
            buffer.append(contentsOf: bytes[0..<count])
        }
    }
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    let connection: Connection
    private var sequence = 0
    private var lastMeter = 0.0

    init(connection: Connection) { self.connection = connection }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        try? connection.send(type: "error", fields: ["code": "stream_stopped", "message": error.localizedDescription])
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid, let pcm = Self.int16Mono(sampleBuffer) else { return }
        let timestamp = max(0, CMSampleBufferGetPresentationTimeStamp(sampleBuffer).seconds)
        let monotonic = ProcessInfo.processInfo.systemUptime
        do {
            try connection.send(type: "audio", fields: [
                "sequence": sequence,
                "source_timestamp": timestamp,
                "monotonic_timestamp": monotonic,
                "sample_rate": 16_000,
                "channels": 1,
                "sample_format": "s16le",
            ], payload: pcm)
            sequence += 1
            if monotonic - lastMeter >= 0.1 {
                try connection.send(type: "meter", fields: ["rms": Self.rms(pcm)])
                lastMeter = monotonic
            }
        } catch {
            // The command loop observes the same closed socket and tears down SCStream.
        }
    }

    private static func int16Mono(_ sampleBuffer: CMSampleBuffer) -> Data? {
        guard let format = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbdPointer = CMAudioFormatDescriptionGetStreamBasicDescription(format) else { return nil }
        let asbd = asbdPointer.pointee
        var needed = 0
        var blockBuffer: CMBlockBuffer?
        var stack = AudioBufferList(mNumberBuffers: 1, mBuffers: AudioBuffer())
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: &needed,
            bufferListOut: &stack,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: 0,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr, let data = stack.mBuffers.mData else { return nil }
        let sampleCount = Int(stack.mBuffers.mDataByteSize) / Int(asbd.mBytesPerFrame)
        if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 {
            let values = data.bindMemory(to: Float.self, capacity: sampleCount)
            var output = [Int16](repeating: 0, count: sampleCount)
            for index in 0..<sampleCount {
                output[index] = Int16(max(-32768, min(32767, Int(values[index] * 32767))))
            }
            return output.withUnsafeBytes { Data($0) }
        }
        if asbd.mBitsPerChannel == 16 {
            return Data(bytes: data, count: Int(stack.mBuffers.mDataByteSize))
        }
        return nil
    }

    private static func rms(_ pcm: Data) -> Double {
        pcm.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            guard !samples.isEmpty else { return 0 }
            let sum = samples.reduce(0.0) { value, sample in
                let normalized = Double(sample) / 32768.0
                return value + normalized * normalized
            }
            return min(1, sqrt(sum / Double(samples.count)))
        }
    }
}

@available(macOS 13.0, *)
private func makeFilter(target: [String: Any]) async throws -> SCContentFilter {
    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
    if let windowID = target["window_id"] as? Int,
       let window = content.windows.first(where: { Int($0.windowID) == windowID }) {
        return SCContentFilter(desktopIndependentWindow: window)
    }
    let application = content.applications.first { app in
        if let pid = target["process_id"] as? Int, Int(app.processID) == pid { return true }
        if let bundleID = target["bundle_id"] as? String, app.bundleIdentifier == bundleID { return true }
        return false
    }
    guard let display = content.displays.first, let application else {
        throw HelperError.message("capture target is not currently shareable")
    }
    return SCContentFilter(display: display, including: [application], exceptingWindows: [])
}

private func listeningSocket(path: String) throws -> Int32 {
    if path.utf8.count >= MemoryLayout.size(ofValue: sockaddr_un().sun_path) {
        throw HelperError.message("Unix socket path is too long")
    }
    unlink(path)
    let descriptor = socket(AF_UNIX, SOCK_STREAM, 0)
    guard descriptor >= 0 else { throw HelperError.message("cannot create Unix socket") }
    var address = sockaddr_un()
    address.sun_family = sa_family_t(AF_UNIX)
    withUnsafeMutablePointer(to: &address.sun_path) { pointer in
        pointer.withMemoryRebound(to: CChar.self, capacity: path.utf8.count + 1) { destination in
            _ = path.withCString { source in strcpy(destination, source) }
        }
    }
    let result = withUnsafePointer(to: &address) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
            bind(descriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size))
        }
    }
    guard result == 0 else {
        Darwin.close(descriptor)
        throw HelperError.message("cannot bind Unix socket: \(errno)")
    }
    guard chmod(path, mode_t(S_IRUSR | S_IWUSR)) == 0 else {
        unlink(path)
        Darwin.close(descriptor)
        throw HelperError.message("cannot restrict Unix socket permissions: \(errno)")
    }
    guard listen(descriptor, 1) == 0 else {
        unlink(path)
        Darwin.close(descriptor)
        throw HelperError.message("cannot listen on Unix socket: \(errno)")
    }
    return descriptor
}

@main
struct Main {
    static func main() async {
        guard #available(macOS 13.0, *) else {
            fputs("ScreenCaptureKit macOS 13+ is required\n", stderr)
            exit(2)
        }
        if CommandLine.arguments.contains("--list-targets") {
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(
                    false, onScreenWindowsOnly: false
                )
                let targets: [[String: Any]] = content.applications.map { application in
                    [
                        "display_name": application.applicationName,
                        "bundle_id": application.bundleIdentifier,
                        "process_id": Int(application.processID),
                    ]
                }
                let data = try JSONSerialization.data(withJSONObject: targets, options: [.sortedKeys])
                FileHandle.standardOutput.write(data)
                exit(0)
            } catch {
                fputs("Target discovery failed: \(error.localizedDescription)\n", stderr)
                exit(3)
            }
        }
        guard let socketPath = ProcessInfo.processInfo.environment["WHISPERED_CAPTURE_SOCKET"] else {
            fputs("ScreenCaptureKit macOS 13+ and WHISPERED_CAPTURE_SOCKET are required\n", stderr)
            exit(2)
        }
        var server: Int32 = -1
        var activeConnection: Connection?
        do {
            server = try listeningSocket(path: socketPath)
            defer { Darwin.close(server); unlink(socketPath) }
            let client = accept(server, nil, nil)
            guard client >= 0 else { throw HelperError.message("cannot accept client") }
            let connection = Connection(descriptor: client)
            activeConnection = connection
            try connection.send(type: "hello", fields: [
                "helper_pid": ProcessInfo.processInfo.processIdentifier,
                "capabilities": ["audio", "screen_capture_kit", "application_filter"],
            ])
            let start = try connection.nextControl()
            guard start["type"] as? String == "start",
                  let target = start["target"] as? [String: Any] else {
                throw HelperError.message("expected start command")
            }
            let filter = try await makeFilter(target: target)
            let configuration = SCStreamConfiguration()
            configuration.capturesAudio = true
            configuration.excludesCurrentProcessAudio = true
            configuration.sampleRate = 16_000
            configuration.channelCount = 1
            configuration.width = 2
            configuration.height = 2
            configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
            let output = AudioOutput(connection: connection)
            let stream = SCStream(filter: filter, configuration: configuration, delegate: output)
            try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: DispatchQueue(label: "audio"))
            try await stream.startCapture()
            try connection.send(type: "started", fields: ["sample_rate": 16_000, "channels": 1])
            while true {
                let control = try connection.nextControl()
                if control["type"] as? String == "stop" { break }
                if control["type"] as? String == "ping" { try connection.send(type: "pong") }
            }
            try await stream.stopCapture()
            try connection.send(type: "stopped")
        } catch {
            let message = String(describing: error)
            let lower = message.lowercased()
            let code = lower.contains("permission") || lower.contains("denied")
                ? "screen_recording_denied" : "capture_failed"
            try? activeConnection?.send(
                type: "error", fields: ["code": code, "message": message]
            )
            fputs("whispered-capture-helper: \(error)\n", stderr)
            exit(1)
        }
    }
}

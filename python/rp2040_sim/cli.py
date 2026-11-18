import argparse
from dataclasses import asdict
from pathlib import Path
import json
import sys


def parser():
    p = argparse.ArgumentParser(description="RP2040 firmware simulator")
    commands = p.add_subparsers(dest="command")
    gui = commands.add_parser("gui", help="Open the desktop simulator (default)")
    gui.add_argument("--project", type=Path)
    gui.add_argument("--screenshot", type=Path, help="Save an offscreen-capable smoke-test screenshot and exit")
    run = commands.add_parser("run", help="Run firmware headlessly")
    run.add_argument("firmware", nargs="?", type=Path, help="ELF, RP2040 UF2 or flash binary; defaults to bundled demo")
    run.add_argument("--project", type=Path)
    run.add_argument("--duration-ms", type=float, default=1000)
    run.add_argument("--clock-hz", type=int)
    run.add_argument("--startup", choices=("direct", "sdk", "rom"), help="Startup contract; rom executes the real B2 ROM and boot2")
    run.add_argument("--bootrom", type=Path, help="Official B2 ROM binary for ROM startup")
    run.add_argument("--trace", type=Path, help="Write complete timestamped events as JSONL")
    run.add_argument("--replay", type=Path)
    run.add_argument("--script", type=Path, help="Execute Python source over the MicroPython UART raw REPL")
    run.add_argument("--save-flash", type=Path, help="Save the final flash binary, including MicroPython files")
    build = commands.add_parser("build", help="Compile a single C source file")
    build.add_argument("source", type=Path)
    build.add_argument("-o", "--output", type=Path, default=Path("build/firmware.elf"))
    build.add_argument("--linker", type=Path)
    build.add_argument("--startup", choices=("direct", "sdk"), default="direct")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command in (None, "gui"):
            from .gui.app import launch
            return launch(getattr(args, "project", None), getattr(args, "screenshot", None))
        if args.command == "build":
            from .build import build, SdkFirmwareBuild
            from . import demo
            if args.startup == "sdk":
                if args.linker is not None:
                    raise ValueError("SDK builds use the SDK linker script; omit --linker")
                print(SdkFirmwareBuild(args.source, args.output).finish(), end="")
                print(args.output)
                return 0
            linker = args.linker
            if linker is None:
                linker = args.output.parent / "direct.ld"
                linker.parent.mkdir(parents=True, exist_ok=True)
                linker.write_text(demo.linker())
            print(build(args.source, args.output, linker), end="")
            print(args.output)
            return 0
        from .controller import Simulation
        from .engine import Stop
        from .project import Project
        from . import demo
        import math
        if not math.isfinite(args.duration_ms) or not 0 < args.duration_ms <= 86_400_000:
            raise ValueError("duration-ms must be positive and at most one day")
        project = Project.load(args.project) if args.project else Project.demo()
        if args.clock_hz is not None:
            project.clock_hz = args.clock_hz
        if args.startup is not None:
            project.startup = args.startup
        elif args.project is None and args.firmware and args.firmware.suffix.lower() in ('.uf2', '.bin'):
            project.startup = 'rom'
        if args.bootrom is not None:
            project.bootrom = str(args.bootrom.resolve())
        if args.script and args.replay:
            raise ValueError('Use either --script or --replay')
        if args.script and project.startup != 'rom':
            raise ValueError('--script requires ROM startup and MicroPython UART firmware')
        simulation = Simulation(project)
        trace = None
        try:
            image = args.firmware or (project.resolve(project.firmware) if project.firmware else None)
            simulation.load(image or demo.image(project.builtin))
            deadline = int(args.duration_ms * 1_000_000)
            trace = args.trace.open("w") if args.trace else None
            transfer = None
            if args.script:
                from .repl import PythonTransfer
                transfer = PythonTransfer(args.script.read_text())

            def consume(events):
                if transfer:
                    transfer.consume(simulation, events)
                for event in events:
                    if trace:
                        trace.write(json.dumps(asdict(event)) + "\n")
                    if event.kind == 2:
                        sys.stdout.write(chr(event.value))

            if args.replay:
                consume(simulation.replay(args.replay, deadline))
            else:
                while simulation.engine.snapshot().time_ns < deadline:
                    if transfer:
                        transfer.advance(simulation)
                    batch_deadline = min(deadline, simulation.engine.snapshot().time_ns + 10_000_000) if transfer else deadline
                    reason = simulation.run(batch_deadline)
                    consume(simulation.engine.events())
                    if transfer and transfer.done:
                        break
                    if reason not in (Stop.BUDGET, Stop.DEADLINE):
                        raise RuntimeError(f"Execution stopped: {reason.name}")
                if transfer and not transfer.done:
                    raise RuntimeError('Python program did not finish before duration-ms; increase the virtual-time limit')
                if transfer and transfer.stderr:
                    raise RuntimeError('Python execution failed: ' + transfer.stderr.decode('utf-8', 'replace'))
            state = simulation.engine.snapshot()
            if state.dropped_events:
                raise RuntimeError(f"Trace overflow: {state.dropped_events} events lost")
            if args.save_flash:
                args.save_flash.write_bytes(simulation.engine.flash_image())
            print(json.dumps({"time_ns": state.time_ns, "instructions": state.instructions,
                              "pc": f"0x{state.registers[15]:08x}", "gpio25": state.pin(25).name}),
                  file=sys.stderr)
            return 0
        finally:
            if trace:
                trace.close()
            simulation.close()
    except (RuntimeError, ValueError, OSError, ImportError) as error:
        print(f"rp2040-sim: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

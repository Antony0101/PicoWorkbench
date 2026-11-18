cmake_minimum_required(VERSION 3.25)
project(rp2040_sim VERSION 0.1.0 LANGUAGES C)
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
find_package(Python3 REQUIRED COMPONENTS Interpreter)
execute_process(COMMAND "${Python3_EXECUTABLE}" -c
    "import pathlib,unicorn; print(pathlib.Path(unicorn.__file__).parent)"
    OUTPUT_VARIABLE UNICORN_ROOT OUTPUT_STRIP_TRAILING_WHITESPACE
    COMMAND_ERROR_IS_FATAL ANY)
add_library(unicorn SHARED IMPORTED)
set_target_properties(unicorn PROPERTIES
    IMPORTED_LOCATION "${UNICORN_ROOT}/lib/libunicorn.so.2"
    INTERFACE_INCLUDE_DIRECTORIES "${UNICORN_ROOT}/include")
add_library(rp2040_sim SHARED
    engine/src/sim.c
    engine/src/cpu.c
    engine/src/bus.c
    engine/src/scheduler.c
    engine/src/gpio.c
    engine/src/peripherals.c
    engine/src/pio.c
    engine/src/dma.c
    engine/src/multicore.c
    engine/src/system.c
    engine/src/bootrom.c)
target_sources(rp2040_sim PRIVATE engine/src/image.c engine/src/flash.c engine/src/rtc.c)
target_include_directories(rp2040_sim PUBLIC engine/include)
target_link_libraries(rp2040_sim PRIVATE unicorn)
target_compile_options(rp2040_sim PRIVATE -Wall -Wextra -Werror)
option(SIM_SANITIZE "Enable address and undefined behavior sanitizers" OFF)
if(SIM_SANITIZE)
    target_compile_options(rp2040_sim PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    target_link_options(rp2040_sim PUBLIC -fsanitize=address,undefined)
endif()
enable_testing()
add_executable(engine_test engine/tests/test_engine.c)
target_link_libraries(engine_test PRIVATE rp2040_sim)
target_compile_options(engine_test PRIVATE -Wall -Wextra -Werror -UNDEBUG)
add_test(NAME engine_contract COMMAND engine_test)

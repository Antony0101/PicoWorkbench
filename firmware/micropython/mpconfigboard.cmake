# Pinned MicroPython RP2040 profile; standard port with UART REPL and USB off.
set(PICO_BOARD pico)
set(PICO_PLATFORM rp2040)
set(MICROPY_FROZEN_MANIFEST ${CMAKE_CURRENT_LIST_DIR}/manifest.py)
add_compile_definitions(PICO_CXX_DISABLE_ALLOCATION_OVERRIDES=1)
cmake_language(DEFER CALL set_target_properties firmware PROPERTIES LINKER_LANGUAGE C)
cmake_language(DEFER CALL set_property TARGET firmware APPEND PROPERTY LINK_LIBRARIES m)

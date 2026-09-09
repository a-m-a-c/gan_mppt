cmake_minimum_required(VERSION 3.22)

#
# This file is generated only once,
# and is not re-generated if converter is called multiple times.
#
# User is free to modify the file as much as necessary
#

# Setup compiler settings
set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS ON)


# Define the build type
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Debug")
endif()

# Set the project name
set(CMAKE_PROJECT_NAME gan_mppt_firmware)

# Enable compile command to ease indexing with e.g. clangd
set(CMAKE_EXPORT_COMPILE_COMMANDS TRUE)

# Core project settings
project(${CMAKE_PROJECT_NAME})
message("Build type: " ${CMAKE_BUILD_TYPE})

# Enable CMake support for ASM and C languages
enable_language(C ASM)

# Create an executable object type
add_executable(${CMAKE_PROJECT_NAME})

# Add STM32CubeMX generated sources
add_subdirectory(cmake/stm32cubemx)

# Link directories setup
target_link_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined library search paths
)

# Add sources to executable
target_sources(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user sources here
    Src/app/channel.c
    Src/app/system.c
    Src/drivers/pwm.c
    Src/drivers/led.c
    Src/drivers/analog.c
    Src/drivers/interrupts.c
    Src/drivers/ina228.c
    Src/drivers/channel_telem.c
    Src/app/app.c
    Src/app/check.c
    Src/app/status.c
    Src/app/modes/mode.c
    Src/app/command.c
    Src/app/stream.c
    Src/app/modes/mode_mppt.c
    Src/app/modes/mode_single_ch_mppt.c
    Src/app/modes/mode_single_ch_cv.c
    Src/app/modes/mode_single_ch_iv_sweep.c
    Src/drivers/serial.c
    Src/app/control/control.c
    Src/app/control/pi.c
    Src/app/control/perturb_observe.c
)

# Add include paths
target_include_directories(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined include paths
    Inc/drivers
    Inc/app
    Inc/app/modes
    Inc/app/control
)

# Add project symbols (macros)
target_compile_definitions(${CMAKE_PROJECT_NAME} PRIVATE
    # Add user defined symbols
)

# Remove wrong libob.a library dependency when using cpp files
list(REMOVE_ITEM CMAKE_C_IMPLICIT_LINK_LIBRARIES ob)

# Add linked libraries
target_link_libraries(${CMAKE_PROJECT_NAME}
    stm32cubemx

    # Add user defined libraries
)

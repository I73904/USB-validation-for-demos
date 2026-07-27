# Install script for directory: D:/ZephyrProject/mchp_zephyrproject/zephyr

# Set the install prefix
if(NOT DEFINED CMAKE_INSTALL_PREFIX)
  set(CMAKE_INSTALL_PREFIX "C:/Program Files (x86)/Zephyr-Kernel")
endif()
string(REGEX REPLACE "/$" "" CMAKE_INSTALL_PREFIX "${CMAKE_INSTALL_PREFIX}")

# Set the install configuration name.
if(NOT DEFINED CMAKE_INSTALL_CONFIG_NAME)
  if(BUILD_TYPE)
    string(REGEX REPLACE "^[^A-Za-z0-9_]+" ""
           CMAKE_INSTALL_CONFIG_NAME "${BUILD_TYPE}")
  else()
    set(CMAKE_INSTALL_CONFIG_NAME "")
  endif()
  message(STATUS "Install configuration: \"${CMAKE_INSTALL_CONFIG_NAME}\"")
endif()

# Set the component getting installed.
if(NOT CMAKE_INSTALL_COMPONENT)
  if(COMPONENT)
    message(STATUS "Install component: \"${COMPONENT}\"")
    set(CMAKE_INSTALL_COMPONENT "${COMPONENT}")
  else()
    set(CMAKE_INSTALL_COMPONENT)
  endif()
endif()

# Is this installation the result of a crosscompile?
if(NOT DEFINED CMAKE_CROSSCOMPILING)
  set(CMAKE_CROSSCOMPILING "TRUE")
endif()

# Set path to fallback-tool for dependency-resolution.
if(NOT DEFINED CMAKE_OBJDUMP)
  set(CMAKE_OBJDUMP "C:/Users/I73904/zephyr-sdk-1.0.1/gnu/arm-zephyr-eabi/bin/arm-zephyr-eabi-objdump.exe")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/arch/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/lib/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/soc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/boards/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/subsys/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/drivers/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/microchip-zsdk/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/microchip/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/cmsis/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/cmsis-dsp/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/cmsis-nn/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/cmsis_6/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/fatfs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/atmel/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/littlefs/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/lvgl/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/mbedtls/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/mcuboot/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/picolibc/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/segger/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/modules/zcbor/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/kernel/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/cmake/flash/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/cmake/usage/cmake_install.cmake")
endif()

if(NOT CMAKE_INSTALL_LOCAL_ONLY)
  # Include the install script for the subdirectory.
  include("D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/cmake/reports/cmake_install.cmake")
endif()

string(REPLACE ";" "\n" CMAKE_INSTALL_MANIFEST_CONTENT
       "${CMAKE_INSTALL_MANIFEST_FILES}")
if(CMAKE_INSTALL_LOCAL_ONLY)
  file(WRITE "D:/ZephyrProject/usb_validation/results_blinky/.build_cache/pic32ck_sg01_cult_blinky_na/zephyr/install_local_manifest.txt"
     "${CMAKE_INSTALL_MANIFEST_CONTENT}")
endif()

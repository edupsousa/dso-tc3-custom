# Makefile for DSO-TC3 Custom Firmware Extension

# Use local toolchain if present, otherwise system arm-none-eabi-*
TOOLCHAIN_BIN ?= $(if $(wildcard tools/toolchain/bin/arm-none-eabi-gcc),tools/toolchain/bin/,)
CC      := $(TOOLCHAIN_BIN)arm-none-eabi-gcc
OBJCOPY := $(TOOLCHAIN_BIN)arm-none-eabi-objcopy
OBJDUMP := $(TOOLCHAIN_BIN)arm-none-eabi-objdump
SIZE    := $(TOOLCHAIN_BIN)arm-none-eabi-size

CFLAGS  := -mcpu=cortex-m4 -mthumb -mfloat-abi=soft -O2 -Wall -Wextra -Werror -fno-strict-aliasing \
           -ffreestanding -fno-builtin -nostartfiles \
           -Iinclude

LDFLAGS := -T extension.ld -nostdlib -Wl,--gc-sections -Wl,-Map=build/extension.map

SRC     := $(wildcard src/*.c)
OBJ     := $(SRC:src/%.c=build/%.o)
TARGET  := build/extension

all: $(TARGET).bin $(TARGET).dis

# -MMD: header changes rebuild the objects that include them
build/%.o: src/%.c | build
	$(CC) $(CFLAGS) -MMD -MP -c $< -o $@

-include $(OBJ:.o=.d)

$(TARGET).elf: $(OBJ) extension.ld
	$(CC) $(CFLAGS) $(OBJ) $(LDFLAGS) -o $@
	$(SIZE) $@

$(TARGET).bin: $(TARGET).elf
	$(OBJCOPY) -O binary $< $@

$(TARGET).dis: $(TARGET).elf
	$(OBJDUMP) -d -Mforce-thumb $< > $@

build:
	mkdir -p build

clean:
	rm -rf build

.PHONY: all clean

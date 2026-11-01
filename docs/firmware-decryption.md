# FNIRSI DSO-TC3 — Firmware Decryption Process

This document describes how the shipped firmware file turns into a flashable
image. It draws only from the decryption-relevant parts of `NOTES.md`. Source:
2026-10-22 analysis session.

## 1. The shipped file is not an S-record

The vendor ships `firmware/stock/V0.3/DSO-TC3_V0.3.s19`. The `.s19`
extension suggests the Motorola S-record format, a text format for firmware
images. The file is not that format. It is a raw binary flash image with a
header in front, and the payload is scrambled with a simple cipher.

The file is 365,964 bytes (0x5958C in hexadecimal, a base-16 number system
common in firmware work).

## 2. File layout

The file has two parts.

| Range | Content |
|---|---|
| Byte 0x00 to 0x1F (32 bytes) | Vendor header, not encrypted |
| Byte 0x20 to the end | Flash payload, encrypted |

The header reads `d7 86 10 56 33 1b`, then the byte `0x2c` repeated 24 times,
then `e6 c8`. The header is the first update packet. `h[0]^h[1]^h[0x1E]^h[0x1F]` is the
XOR key applied to the whole payload (`d7^86^e6^c8 = 0x7f` for stock);
`h[2..5]^key` is a magic the bootloader checks (`{6f,29,4c,64}` expected);
`h[6..9]` is read as a 32-bit file size — display only, used as the divisor of
the `Sys.State.FileSize:%d` progress print (stock placeholder `0x2C2C2C2C`).
The header is therefore a key + magic + size block, and none of it is written to flash.
Standard checksum methods tested against the two trailing bytes in the original
analysis (CRC16, CRC32, Adler, byte sum) had already ruled out a plain
checksum role for the header as a whole.

## 3. The encryption algorithm

The payload uses a single-byte XOR cipher. XOR (exclusive OR) is a bit
operation: it flips each bit of a byte based on the matching bit of a key
byte. The key here is one byte, `0x7f`, applied the same way to every byte of
the payload, independent of its position.

The formula is:

```
plain[i] = file[0x20 + i] ^ 0x7f
```

Each decrypted byte equals the corresponding file byte, from offset 0x20
onward, exclusive-ORed with `0x7f`.

### 3.1 Evidence that this is correct

Two independent checks support the key and the method.

- The ARM vector table reserves slots that must decrypt to zero. They do.
- The decrypted payload contains 858 readable text strings (menu text, error
  messages, and similar). A wrong key produces far fewer readable strings
  than this, by chance alone.

## 4. What the decrypted payload contains

After decryption, the payload is a flash image that the microcontroller
expects at flash address `0x08000000`.

The image starts with two duplicated sets of eight interrupt vectors, at
image offset `0x00` and again at offset `0x20`. Each set holds: stack
pointer, reset vector, and the handlers for NMI, hard fault, memory
management fault, bus fault, usage fault, and one reserved zero slot.

The main vector table follows at image offset `0x20` (file offset `0x40`),
with the reserved slots reading zero as expected.

Key vectors recovered from the decrypted image:

| Vector | Address |
|---|---|
| Stack pointer | `0x20018ef8` |
| Reset | `0x080041b1` |
| NMI | `0x08008769` |
| Hard fault | `0x08007b31` |
| PendSV | `0x0800cf3f` |
| SysTick | `0x08001a6d` |
| Default IRQ handler | `0x0800cf33` |

## 5. Where decryption happens

The vendor update tool does not decrypt the file. Its `downloader.pyd`
module reads the `.s19` file and sends the bytes unchanged, in 64-byte
packets over a USB CDC serial connection, to the device.

The device's bootloader is the component that applies the XOR decryption,
during the write to flash:
the bootloader derives the key from the first update packet — the 32-byte
header — checks a magic, then XORs every subsequent payload byte with that
key before programming it. The update file never contains the bootloader
itself, so it survives every firmware update and remains a built-in recovery
path.

## 6. Flash layout

The device internal flash (512 KB) is organized as follows:

| Range | Content |
|---|---|
| `0x08000000`–`0x08003FFF` (16 KB) | Bootloader (permanent, runs on DOWN+POWER) |
| `0x08004000`–`0x0805D54B` (~357 KB) | Application payload (written from `.s19` offset `0x40`) |
| `0x0805D54C`–`0x0805DFFF` | Erased / available padding |
| `0x0805E000`–`0x0807EFFF` | Custom extension code & buffers |
| `0x0807F000`–`0x0807FFFF` (4 KB) | Calibration and configuration sector (do not overwrite!) |

## 7. Python decryption helper

```python
data = open("DSO-TC3_V0.3.s19", "rb").read()
key = data[0] ^ data[1] ^ data[0x1E] ^ data[0x1F]  # 0x7F on stock
flash = bytes(b ^ key for b in data[0x20:])
```

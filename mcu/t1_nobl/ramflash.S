@ RAM flasher for an STM32F103 mainboard without a bootloader (regular T1).
@
@ mcu/t1_flash.py writes this into RAM the running firmware doesn't use
@ (above 0x20005000), fills in the operation list at the end, then points
@ VTOR at a RAM vector table whose entries all lead here. The next interrupt
@ runs it: interrupts off, then each operation in turn, then a reset. It
@ never returns, and it doesn't read flash while it erases or programs (it
@ runs from RAM), so it can rewrite the pages the old firmware runs from.
@
@ Operation list (OPS, 4 words each, ends with op 0):
@   1 addr count 0     erase `count` 2 KB pages from `addr`
@   2 src dst bytes    copy halfwords src -> dst (0xFFFF halfwords skipped)
@   0                  end: reset the chip
@
@ This file may be distributed under the terms of the GNU GPLv3 license.
        .syntax unified
        .cpu cortex-m3
        .thumb
        .text
        .global ramflash
        .type ramflash, %function
ramflash:
        cpsid   i
        ldr     r7, =0x40022000         @ FLASH
        ldr     r6, =0x40003000         @ IWDG
        ldr     r5, =0xAAAA             @ watchdog reload key
        str     r5, [r6]
        ldr     r0, [r7, #0x10]         @ CR
        tst     r0, #0x80               @ LOCK?
        beq     1f
        ldr     r0, =0x45670123
        str     r0, [r7, #0x04]         @ KEYR
        ldr     r0, =0xCDEF89AB
        str     r0, [r7, #0x04]
1:      movs    r0, #0x34               @ clear EOP, WRPRTERR, PGERR
        str     r0, [r7, #0x0C]
        adr     r4, ops
next:   ldr     r0, [r4, #0]
        ldr     r1, [r4, #4]
        ldr     r2, [r4, #8]
        ldr     r3, [r4, #12]
        adds    r4, #16
        cmp     r0, #1
        beq     erase
        cmp     r0, #2
        beq     copy
        @ end (or anything unknown): lock the flash and reset
        movs    r0, #0x80
        str     r0, [r7, #0x10]
        dsb
        ldr     r0, =0xE000ED0C         @ AIRCR
        ldr     r1, =0x05FA0004         @ VECTKEY | SYSRESETREQ
        str     r1, [r0]
        dsb
hang:   b       hang

erase:  cmp     r2, #0
        beq     next
        movs    r0, #0x02               @ PER
        str     r0, [r7, #0x10]
        str     r1, [r7, #0x14]         @ AR
        movs    r0, #0x42               @ PER | STRT
        str     r0, [r7, #0x10]
        bl      wait
        movs    r0, #0
        str     r0, [r7, #0x10]
        add     r1, r1, #0x800
        subs    r2, #1
        b       erase

copy:   movs    r0, #0x01               @ PG
        str     r0, [r7, #0x10]
2:      cmp     r3, #0
        ble     3f
        ldrh    r0, [r1]
        movw    r12, #0xFFFF
        cmp     r0, r12
        beq     4f
        strh    r0, [r2]
        bl      wait
4:      adds    r1, #2
        adds    r2, #2
        subs    r3, #2
        b       2b
3:      movs    r0, #0
        str     r0, [r7, #0x10]
        b       next

        @ Waits until the flash isn't busy, reloading the watchdog.
wait:   str     r5, [r6]
        ldr     r0, [r7, #0x0C]         @ SR
        tst     r0, #0x01               @ BSY
        bne     wait
        bx      lr

        .ltorg
        .balign 16
ops:    @ filled in by t1_flash.py
        .space  16*8

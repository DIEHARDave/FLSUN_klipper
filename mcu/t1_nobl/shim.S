@ Start shim for page 0 of a regular T1 mainboard (no bootloader).
@
@ The chip always starts from 0x08000000. This page only hands over to the
@ Klipper image at 0x08007000, the same address and image as on the T1 Pro
@ (where FLSun's MKS bootloader sits in front). Klipper sets VTOR to its own
@ vector table as it starts, so nothing else here is used. Written once;
@ firmware updates only change 0x08007000 onwards.
@
@ This file may be distributed under the terms of the GNU GPLv3 license.
        .syntax unified
        .cpu cortex-m3
        .thumb
        .section .vectors, "a"
        .word   0x20005000              @ stack until the image sets its own
        .word   reset + 1
        .rept   14
        .word   hang + 1                @ NMI, HardFault ... SysTick
        .endr
        .text
        .global reset
        .type reset, %function
reset:  ldr     r0, =0x08007000
        ldr     r1, [r0, #0]
        msr     msp, r1
        ldr     r1, [r0, #4]
        bx      r1
        .type hang, %function
hang:   b       hang
        .ltorg

// SPDX-License-Identifier: GPL-3.0-or-later
// Android AArch64 guest-call trampoline for Stage 1C.

    .text
    .align 4
    .global nx2_guest_call
    .type nx2_guest_call, %function

// uint64_t nx2_guest_call(void *fn, uint64_t arg0, uint64_t arg1, void *stack_top)
//
// x0 = function
// x1 = guest arg0
// x2 = guest arg1
// x3 = guest stack top
nx2_guest_call:
    sub sp, sp, #32
    stp x19, x20, [sp, #0]
    str x30, [sp, #16]

    mov x20, sp
    mov x16, x0
    mov x0, x1
    mov x1, x2
    mov sp, x3
    blr x16

    mov sp, x20
    ldr x30, [sp, #16]
    ldp x19, x20, [sp, #0]
    add sp, sp, #32
    ret

    .size nx2_guest_call, .-nx2_guest_call

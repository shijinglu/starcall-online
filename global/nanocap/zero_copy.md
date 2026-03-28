

# "zero copy"

## The Core Idea

At its heart, zero copy means **avoiding unnecessary copies of data as it moves from producer to consumer**. Every `memcpy` costs CPU cycles and cache pollution, and at high throughput those costs dominate. So any technique that eliminates a copy can claim the "zero copy" label — but *which* copy is being eliminated varies widely.

---

## Cap'n Proto — Zero-Copy *Serialization*

Cap'n Proto's zero-copy claim is about the **serialization/deserialization boundary**. Traditional formats like Protocol Buffers or JSON require you to decode a wire-format message into an in-memory object (a copy), do your work, then encode your response back (another copy). Cap'n Proto eliminates both steps: the in-memory layout *is* the wire format. You read fields directly out of the received buffer with pointer arithmetic — no decode pass, no intermediate object, no copy.

So the "copy" being eliminated is the **encode/decode copy between wire format and application-visible structs**. The data might still be copied by the OS from a network buffer into userspace; Cap'n Proto doesn't concern itself with that layer.

---

## NNG (nanomsg-next-gen) — Zero-Copy *Message Passing*

NNG's zero-copy operates at the **messaging/transport layer**. When you send a message between threads or between processes on the same machine, a naïve implementation would copy the payload into a send buffer, then copy it again into the receiver's buffer. NNG avoids this by passing ownership of a reference-counted buffer — internally the message body is allocated from a special pool, and "sending" it just hands the pointer (and ownership) to the receiver. No payload bytes move.

The "copy" being eliminated is the **buffer-to-buffer copy inside the messaging library itself**. Over a network socket the OS will still do its normal DMA and buffer management, so the zero-copy benefit is strongest for intra-process (thread-to-thread) and inter-process (shared-memory) scenarios.

---

## Kafka — Zero-Copy *Network I/O*

Kafka's zero-copy claim is about the **OS/kernel layer**. When a Kafka broker serves a fetch request, it needs to send log data from a file on disk out through a network socket. The traditional path is: disk → kernel page cache → userspace buffer → kernel socket buffer → NIC. Kafka uses `sendfile()` (or the Java equivalent, `FileChannel.transferTo()`) to shortcut this to: disk → kernel page cache → NIC. The data never enters userspace at all.

The "copy" being eliminated is the **kernel-to-userspace-and-back copy during network transmission of on-disk data**. This is a completely different layer than what Cap'n Proto or NNG are talking about.

---

## Comparison at a Glance

| | Which copy is avoided? | Layer | Key mechanism |
|---|---|---|---|
| **Cap'n Proto** | Serialize/deserialize into app structs | Application / data format | Wire format = memory layout |
| **NNG** | Payload copy between send and receive buffers | Messaging library | Pointer/ownership transfer of pooled buffers |
| **Kafka** | Userspace round-trip during disk→network serving | OS / kernel | `sendfile()` / DMA from page cache to NIC |

---

# Combine NNG and Capn Protocol

## The Naïve Combination (Still Pretty Good)

The simplest approach is to use both libraries independently without any special glue:

**Sender side:**
1. Build a Cap'n Proto message → produces a flat byte buffer (no serialization cost — Cap'n Proto's zero copy)
2. Create an NNG message, **copy** the Cap'n Proto bytes into it
3. `nng_sendmsg()` — NNG transfers buffer ownership to the receiver (no send-side copy — NNG's zero copy)

**Receiver side:**
4. `nng_recvmsg()` — receives the buffer by pointer transfer
5. Read Cap'n Proto fields directly out of that buffer (no deserialization — Cap'n Proto's zero copy)

You still have **one copy** at step 2, where you move the Cap'n Proto output into an NNG-owned buffer. That's already much better than a traditional setup (which would serialize, copy into send buffer, copy into receive buffer, then deserialize — easily 3–4 copies). But we can do better.

## The Deep Integration (True End-to-End Zero Copy)

The key insight is that both libraries allow you to control memory allocation. If you can get Cap'n Proto to **build its message directly inside an NNG-allocated buffer**, that last remaining copy disappears.

Here's the strategy:

1. **Allocate an NNG message** with `nng_msg_alloc()` up front, giving you a buffer from NNG's pool.
2. **Write the Cap'n Proto message directly into that buffer.** Cap'n Proto's `MessageBuilder` supports custom segment allocators — you write one that hands out space from the NNG message body.
3. **Send the NNG message.** Ownership transfers to the receiver with no copy.
4. **The receiver reads Cap'n Proto fields directly** out of the received NNG buffer. No decode step.

The entire path from "app A populates fields" to "app B reads fields" involves **zero payload copies**.

## What the Custom Allocator Looks Like (Sketch)

In Cap'n Proto (C++ API), you'd do something conceptually like this:

```cpp
// A custom Cap'n Proto segment allocator that writes into an NNG message
class NngMessageAllocator : public capnp::MessageBuilder {
    nng_msg* msg_;
public:
    NngMessageAllocator(nng_msg* msg) : msg_(msg) {}

    kj::ArrayPtr<capnp::word> allocateSegment(uint minimumSize) override {
        // Grow the NNG message body and return a pointer into it
        size_t bytes = minimumSize * sizeof(capnp::word);
        nng_msg_realloc(msg_, nng_msg_len(msg_) + bytes);
        auto* ptr = (capnp::word*)(nng_msg_body(msg_) + nng_msg_len(msg_) - bytes);
        return kj::arrayPtr(ptr, minimumSize);
    }
};

// Usage:
nng_msg* msg;
nng_msg_alloc(&msg, 0);

NngMessageAllocator builder(msg);
auto root = builder.initRoot<MySchema>();
root.setName("hello");
root.setValue(42);

// Send — no copy, the data is already in the NNG buffer
nng_sendmsg(socket, msg, 0);
```

On the receiver side it's simpler — you just point Cap'n Proto's reader at the NNG message body:

```cpp
nng_msg* msg;
nng_recvmsg(socket, &msg, 0);

// Read directly out of the NNG buffer — no copy, no decode
auto reader = capnp::FlatArrayMessageReader(
    kj::arrayPtr((capnp::word*)nng_msg_body(msg),
                  nng_msg_len(msg) / sizeof(capnp::word)));

auto root = reader.getRoot<MySchema>();
std::cout << root.getName().cStr();  // reads directly from NNG buffer
```

## The Full Picture

| Step | Naïve combo | Deep integration |
|---|---|---|
| Build message | Into Cap'n Proto buffer (0 copy) | Directly into NNG buffer (0 copy) |
| Move to NNG | **1 copy** into NNG msg | Already there (0 copy) |
| Transport | Pointer transfer (0 copy) | Pointer transfer (0 copy) |
| Read fields | Direct from buffer (0 copy) | Direct from buffer (0 copy) |
| **Total copies** | **1** | **0** |

## Practical Caveats

There are a few wrinkles to be aware of. Cap'n Proto messages can be multi-segment, and your custom allocator needs to handle that (or you constrain to single-segment messages, which is common for fixed-size schemas). Alignment matters too — Cap'n Proto requires 8-byte aligned word boundaries, and you need to make sure the NNG buffer satisfies that (it usually does, since `nng_msg_alloc` returns well-aligned memory). Finally, the true zero-copy path via NNG only applies for intra-process or shared-memory transports; once you cross a real network socket, the kernel will inevitably copy bytes into the NIC's DMA buffer, and that's a layer neither library controls.

But for the application-level path — building, sending, receiving, and reading a structured message — yes, the combination can genuinely achieve zero copies of the payload data.


## One more example in C 

The Schema File (`namecard.capnp`)

```capnp
@0xabcdef1234567890;

struct NameCard {
    username @0 :Text;
    age      @1 :UInt8;
    email    @2 :Text;
}
```

Compile it with the capnp-c compiler to generate `namecard.capnp.h` and `namecard.capnp.c`.

### The C Code

```c
#include <stdio.h>
#include <string.h>
#include <nng/nng.h>
#include <capnp_c.h>
#include "namecard.capnp.h"

/* ── Helper: wrap a C string as a capnp-c text value ── */
static capn_text text_from_cstr(const char *s) {
    return (capn_text){
        .len = strlen(s),
        .str = s,
        .seg = NULL
    };
}

/* ── Sender ── */
void send_namecard(nng_socket sock,
                   const char *username,
                   uint8_t     age,
                   const char *email)
{
    nng_msg *msg;
    struct capn ctx;

    /* 1. Allocate NNG message — generous initial size */
    size_t buf_size = 4096;
    nng_msg_alloc(&msg, buf_size);

    /* 2. Bind capnp-c context directly to the NNG buffer */
    capn_init_mem(&ctx, (uint8_t *)nng_msg_body(msg), buf_size, 0);

    /* 3. Build the NameCard */
    NameCard_ptr card = new_NameCard(ctx.seglist);
    set_NameCard_username(card, text_from_cstr(username));
    set_NameCard_age(card, age);
    set_NameCard_email(card, text_from_cstr(email));

    /* 4. Set it as the message root */
    capn_setp(capn_root(&ctx), 0, card.p);

    /* 5. Serialize into the NNG buffer and trim to actual size */
    int used = capn_write_mem(&ctx, (uint8_t *)nng_msg_body(msg),
                              buf_size, 0);
    nng_msg_realloc(msg, (size_t)used);

    /* 6. Send — zero copy, ownership transfers to receiver */
    nng_sendmsg(sock, msg, 0);

    capn_free(&ctx);
}

/* ── Receiver ── */
void recv_namecard(nng_socket sock) {
    nng_msg *msg;

    /* 1. Receive — pointer transfer, no payload copy */
    nng_recvmsg(sock, &msg, 0);

    /* 2. Point capnp-c reader at the NNG buffer */
    struct capn ctx;
    capn_init_mem(&ctx, (uint8_t *)nng_msg_body(msg),
                  nng_msg_len(msg), 0);

    /* 3. Read the root NameCard — just pointer arithmetic, no decode */
    NameCard_ptr card;
    card.p = capn_getp(capn_root(&ctx), 0, 1);

    capn_text username = get_NameCard_username(card);
    uint8_t   age      = get_NameCard_age(card);
    capn_text email    = get_NameCard_email(card);

    printf("Received NameCard:\n");
    printf("  username : %.*s\n", username.len, username.str);
    printf("  age      : %u\n", age);
    printf("  email    : %.*s\n", email.len, email.str);

    /* 4. Clean up */
    capn_free(&ctx);
    nng_msg_free(msg);
}

/* ── Main: simple pair transport demo ── */
int main(void) {
    nng_socket sender, receiver;
    const char *url = "ipc:///tmp/namecard_demo";

    /* Set up a PAIR/PAIR connection */
    nng_pair0_open(&sender);
    nng_pair0_open(&receiver);
    nng_listen(receiver, url, NULL, 0);
    nng_dial(sender, url, NULL, 0);

    /* Send a name card */
    send_namecard(sender, "alice", 30, "alice@example.com");

    /* Receive and print it */
    recv_namecard(receiver);

    nng_close(sender);
    nng_close(receiver);
    return 0;
}
```

### What's Happening Under the Hood

The entire journey of the string `"alice@example.com"` through this code goes like this:

**Sender process:** `text_from_cstr` wraps the pointer → `set_NameCard_email` writes it into the Cap'n Proto segment → that segment *is* the NNG message body → `nng_sendmsg` hands the buffer pointer to the transport layer.

**Receiver process:** `nng_recvmsg` receives the buffer by ownership transfer → `capn_init_mem` points the reader at it → `get_NameCard_email` returns a `capn_text` whose `.str` points directly into the NNG buffer.

No serialization pass, no deserialization pass, no intermediate copies. The `printf` on the receiver side is reading bytes that were written by `set_NameCard_email` on the sender side, sitting in the same NNG-allocated memory the whole time (or its IPC-transferred equivalent).

The one thing to keep in mind with `%.*s` in the printf: Cap'n Proto text fields are null-terminated by spec, so `%s` would work too — but using the explicit length is a safer habit in case you ever switch to a `Data` field (which is raw bytes, not null-terminated).
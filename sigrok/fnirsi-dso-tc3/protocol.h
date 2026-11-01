/*
 * This file is part of the libsigrok project.
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef LIBSIGROK_HARDWARE_FNIRSI_DSO_TC3_PROTOCOL_H
#define LIBSIGROK_HARDWARE_FNIRSI_DSO_TC3_PROTOCOL_H

#include <stdint.h>
#include <glib.h>
#include <libsigrok/libsigrok.h>
#include "libsigrok-internal.h"

#define LOG_PREFIX "fnirsi-dso-tc3"

/* USB CDC identity of the DSO-TC3 running the extension firmware. */
#define TC3_USB_VID		0x19f5
#define TC3_USB_PID		0x5740

/*
 * Extension protocol (docs/host-tools.md, firmware_ext/include/protocol.h, host/tc3_client.py).
 * The wire values below must match the firmware's.
 */
#define TC3_CMD_READ_MEM	0x42	/* 0x42 addr32 len16 -> 0x43 len bytes, at most TC3_READ_MEM_MAX */
#define TC3_CMD_FRAME		0x54
#define TC3_CMD_UI		0x55
#define TC3_CMD_ROLL		0x57
#define TC3_CMD_GEN		0x58
#define TC3_CMD_INFO		0x5f
#define TC3_RESP_READ_MEM	0x43
#define TC3_RESP_ERROR		0x7e	/* 0x7e command code */
#define TC3_READ_MEM_MAX	60

/* Codes of an error reply, and the value devc->last_err takes for a reply that does not parse. */
#define TC3_ERR_BAD_CHUNK	2
#define TC3_ERR_NO_FRAME	4	/* the frame or roll sample left the device's ring, or is not there yet */
#define TC3_ERR_EMPTY		5	/* nothing recorded since boot */
#define TC3_ERR_BAD_REPLY	(-1)

/* Second byte of a frame or roll request that asks for the header, of a UI request that polls. */
#define TC3_HEADER		0xff
#define TC3_ROLL_SAMPLES	1	/* second byte of a roll request for samples */
#define TC3_GEN_GET		0
#define TC3_GEN_SET		1

/* Deferred actions (UI 0x55, generator 0x58): state in byte 2 of the poll reply, result in byte 3. */
#define TC3_ACTION_DONE		2

/* Field ids of the UI command (0x55). */
#define TC3_UI_VDIV		2
#define TC3_UI_PROBE		3
#define TC3_UI_TDIV		4
#define TC3_UI_COUPLING		5
#define TC3_UI_TRIGLEVEL	6
#define TC3_UI_EDGE		7
#define TC3_UI_TRIGMODE		8

/*
 * g_ScopeState (0x20000668). The driver reads it with one memory read of TC3_STATE_LEN bytes from TC3_STATE_FROM;
 * the fields are offsets into the structure.
 */
#define TC3_STATE_BASE		0x20000668
#define TC3_STATE_FROM		0x20
#define TC3_STATE_LEN		0x24
#define TC3_OFF_MODE		0x20	/* 3 = scope view */
#define TC3_OFF_POS		0x24	/* vertical position, the trigger baseline */
#define TC3_OFF_VDIV		0x28	/* index 0..9 */
#define TC3_OFF_PROBE		0x2c	/* 0 = X1, 1 = X10 */
#define TC3_OFF_TDIV		0x30	/* index 0..21 */
#define TC3_OFF_COUPLING	0x34	/* 1 = DC */
#define TC3_OFF_TRIGOFF		0x38	/* trigger level offset, -99..99 */
#define TC3_OFF_TRIGMODE	0x3c	/* 0 Auto, 1 Normal, 2 Single */
#define TC3_OFF_EDGE		0x40	/* 1 = falling */

/* The trigger compares the trace buffer with (position - offset); the trace is 23 units per division, 0 V at 0. */
#define TC3_TRACE_UNITS_PER_DIV	23.0
#define TC3_TRIG_OFF_MAX	99
#define TC3_NUM_TRIGMODES	3

#define TC3_MODE_SCOPE		3

#define TC3_NUM_TDIV		22
#define TC3_NUM_VDIV		10
#define TC3_ROLL_MIN_TDIV	15	/* T/div index 15..21: gap-free roll stream */
#define TC3_FRAME_POINTS	600	/* samples of one burst frame, 25 per division */
#define TC3_FRAME_DIVS		24

/* Generator waveforms of the stock firmware (command 0x58). */
#define TC3_NUM_WAVES		6
#define TC3_GEN_FREQ_MIN	1
#define TC3_GEN_FREQ_MAX	100000
#define TC3_GEN_AMP_MIN		0.01
#define TC3_GEN_AMP_MAX		3.3

struct tc3_state {
	uint32_t mode;
	uint32_t vdiv;		/* index 0..9 */
	uint32_t tdiv;		/* index 0..21 */
	uint32_t probe;		/* 0 = X1, 1 = X10 */
	uint32_t dc;		/* 1 = DC, 0 = AC */
	uint32_t edge;		/* 0 = rising, 1 = falling */
	int32_t pos;		/* vertical position */
	int32_t trigoff;	/* trigger level offset */
	uint32_t trigmode;	/* 0 Auto, 1 Normal, 2 Single */
};

struct tc3_frame_hdr {
	uint32_t seq, latest;
	uint8_t vdiv, tdiv, probe, mode;
	int32_t gain;
	float rate_ksps;
	uint8_t chunks;
};

struct tc3_roll_hdr {
	uint32_t count, base;
	uint8_t epoch, tdiv, vdiv, probe, mode;
	int32_t gain;
	float rate_ksps;
	uint16_t ring;
};

struct tc3_gen {
	uint8_t wave;
	uint32_t freq;
	uint8_t duty;
	float amp;
};

struct dev_context {
	struct tc3_state st;
	struct tc3_gen gen;
	int last_err;		/* device error code of the last failed transfer */
	gboolean link_lost;	/* the port reported an I/O error: unplugged, or the device powered off */
	unsigned int errors;	/* failed polls in a row */

	struct sr_sw_limits limits;

	/* Acquisition state. */
	uint32_t last_seq;		/* newest burst frame already sent */
	gboolean roll_active;
	uint32_t roll_next;		/* next absolute roll sample index */
	uint8_t roll_epoch;
	uint8_t acq_tdiv;		/* T/div the current stream belongs to */
	uint64_t sent_rate;		/* last samplerate meta sent, in Hz */
	unsigned int roll_dup;		/* each roll sample is sent this many times */
	float *buf;
	size_t buf_len;
};

SR_PRIV int tc3_identify(const struct sr_dev_inst *sdi, char *banner, size_t len);
SR_PRIV int tc3_read_state(const struct sr_dev_inst *sdi);
SR_PRIV int tc3_ui_set(const struct sr_dev_inst *sdi, int field, int value);
SR_PRIV int tc3_gen_read(const struct sr_dev_inst *sdi);
SR_PRIV int tc3_gen_write(const struct sr_dev_inst *sdi, const struct tc3_gen *gen);

SR_PRIV double tc3_trigger_level(const struct tc3_state *st);
SR_PRIV int tc3_trigger_offset(const struct tc3_state *st, double volts);
SR_PRIV double tc3_tdiv_seconds(unsigned int idx);
SR_PRIV double tc3_vdiv_volts(unsigned int idx, unsigned int probe);
SR_PRIV double tc3_rate_hz(unsigned int tdiv, float header_rate_ksps);
SR_PRIV void tc3_roll_rate(double hz, uint64_t *meta, unsigned int *dup);

SR_PRIV int fnirsi_dso_tc3_receive_data(int fd, int revents, void *cb_data);

#endif

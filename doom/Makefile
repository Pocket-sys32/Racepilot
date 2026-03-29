UNAME_S := $(shell uname -s)

CC = clang
CFLAGS = -O2 -fPIC -Wall -DNORMALUNIX -DLINUX -DSNDSERV -D_DEFAULT_SOURCE
# Override exit() to prevent doom from killing the process
CFLAGS += -Dexit=doom_exit

DOOM_SRC_DIR = doomgeneric_src/doomgeneric
OBJDIR = build

# All doom source objects (without platform-specific file)
SRC_DOOM = dummy.o am_map.o doomdef.o doomstat.o dstrings.o d_event.o d_items.o \
  d_iwad.o d_loop.o d_main.o d_mode.o d_net.o f_finale.o f_wipe.o g_game.o \
  hu_lib.o hu_stuff.o info.o i_cdmus.o i_endoom.o i_joystick.o i_scale.o \
  i_sound.o i_system.o i_timer.o memio.o m_argv.o m_bbox.o m_cheat.o \
  m_config.o m_controls.o m_fixed.o m_menu.o m_misc.o m_random.o p_ceilng.o \
  p_doors.o p_enemy.o p_floor.o p_inter.o p_lights.o p_map.o p_maputl.o \
  p_mobj.o p_plats.o p_pspr.o p_saveg.o p_setup.o p_sight.o p_spec.o \
  p_switch.o p_telept.o p_tick.o p_user.o r_bsp.o r_data.o r_draw.o \
  r_main.o r_plane.o r_segs.o r_sky.o r_things.o sha1.o sounds.o \
  statdump.o st_lib.o st_stuff.o s_sound.o tables.o v_video.o wi_stuff.o \
  w_checksum.o w_file.o w_main.o w_wad.o z_zone.o w_file_stdc.o \
  i_input.o i_video.o doomgeneric.o

DOOM_OBJS = $(addprefix $(OBJDIR)/, $(SRC_DOOM))

# Our platform callback object
PLATFORM_OBJ = $(OBJDIR)/doomgeneric_openpilot.o

ALL_OBJS = $(DOOM_OBJS) $(PLATFORM_OBJ)

ifeq ($(UNAME_S),Darwin)
  OUTPUT = libdoomgeneric.dylib
  SHARED_FLAGS = -dynamiclib
else
  OUTPUT = libdoomgeneric.so
  SHARED_FLAGS = -shared
endif

all: $(OUTPUT)

$(OUTPUT): $(ALL_OBJS)
	@echo "[Linking $@]"
	$(CC) $(SHARED_FLAGS) $(CFLAGS) $(ALL_OBJS) -o $@ -lm

$(OBJDIR):
	mkdir -p $(OBJDIR)

$(DOOM_OBJS): | $(OBJDIR)

$(OBJDIR)/%.o: $(DOOM_SRC_DIR)/%.c
	@echo "[Compiling $<]"
	$(CC) $(CFLAGS) -c $< -o $@

$(PLATFORM_OBJ): doomgeneric_openpilot.c | $(OBJDIR)
	@echo "[Compiling $<]"
	$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -rf $(OBJDIR)
	rm -f $(OUTPUT)

.PHONY: all clean

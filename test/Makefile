# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = tt_um_kaikino_protocol_emu.sv proto_cfg_serial.sv proto_program_ram.sv proto_pio_engine.sv proto_trace_ram.sv

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)
COMPILE_ARGS 		+= -g2012
export PYTHONPATH := $(PWD)/../tools:$(PWD):$(PYTHONPATH)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
# Every test_*.py module in this directory is a cocotb test module:
COCOTB_TEST_MODULES ?= $(subst $(space),$(comma),$(basename $(notdir $(wildcard $(PWD)/test_*.py))))
comma := ,
space := $(eval) $(eval)

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim

# Plain-Verilog smoke tests (no cocotb needed):  make smoke
SMOKE_TBS = smoke_cfg_tb smoke_pio_tb
smoke:
	@for tb in $(SMOKE_TBS); do \
	  iverilog -g2012 -o sim_build/$$tb $$tb.sv $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES)) && vvp -n sim_build/$$tb | grep -v "finish called" || exit 1; \
	done
.PHONY: smoke

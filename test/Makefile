# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
PROJECT_SOURCES = tt_um_kaikino_protocol_emu.sv proto_cfg_serial.sv proto_program_ram.sv proto_pio_engine.sv proto_trace_ram.sv proto_delay_chain.sv proto_tdc.sv proto_dtc.sv

ifeq ($(FPGA),yes)

# iCE40 post-synthesis netlist simulation (make fpga_netlist first):
SIM_BUILD				= sim_build/fpga
export PROTO_TDLY_PS := 0
VERILOG_SOURCES += $(shell yosys-config --datdir)/ice40/cells_sim.v
VERILOG_SOURCES += $(PWD)/fpga_netlist.v

else ifneq ($(GATES),yes)

# RTL simulation: the delay lines get a behavioural 224 ps/stage model so the
# sub-clock timing features can be checked against the reference model.
SIM_BUILD				= sim_build/rtl
COMPILE_ARGS    += -DTDLY_PS=224
export PROTO_TDLY_PS := 224
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES))

else

# Gate level simulation (the cell models are zero-delay, so the delay lines are too):
SIM_BUILD				= sim_build/gl
export PROTO_TDLY_PS := 0
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

# iCE40 technology-mapped netlist of the FPGA configuration (`SYNTH defined):
fpga_netlist:
	yosys -q -l fpga_synth.log -p "read_verilog -sv -DSYNTH $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES)); synth_ice40 -top tt_um_kaikino_protocol_emu; write_verilog -noattr fpga_netlist.v"
.PHONY: fpga_netlist

# Plain-Verilog smoke tests (no cocotb needed):  make smoke
SMOKE_TBS = smoke_cfg_tb smoke_pio_tb
smoke:
	@for tb in $(SMOKE_TBS); do \
	  iverilog -g2012 -DTDLY_PS=224 -o sim_build/$$tb $$tb.sv $(addprefix $(SRC_DIR)/,$(PROJECT_SOURCES)) && vvp -n sim_build/$$tb | grep -v "finish called" || exit 1; \
	done
.PHONY: smoke

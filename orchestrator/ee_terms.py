#!/usr/bin/env python3
"""
Module:  ee_terms.py
Purpose: Tier 0 — the electrical engineering half of the encyclopedia.
Author:  LB
Date:    2026-08-15

## Why this file exists at all

`define.py` was scoped by D43 from the **math and science rows** of LB's FALL 2026 degree
audit. Those rows do not contain his major — the audit's only EEGR entries are 331 (statistics)
and 161 (C programming). The result, measured on 2026-08-15 before this file existed:

    Tier 0 covered  9 / 74  of his EE curriculum   (12%)
    what is current      -> no entry
    what is resistance   -> no entry
    what is frequency    -> no entry
    what is power        -> answered from physics_mechanics, i.e. work over time

An electrical engineering student's reference tool with no electrical engineering in it. The
scoping rule was followed exactly and still produced that, which is the whole lesson of D47.

A miss here is not a silence: `handled` is False, so the question falls to Tier 1 — the 1.2B
that D30 measured answering these **fluently and wrongly**. Every uncovered term was a chance
to be confidently wrong.

## Why this file holds DATA and imports nothing

`define.py` owns `Term`, the frames and `look_up`. If this module imported `Term` from there
while `define.py` imported this module, the two would be a circular import whose success
depended on statement order inside `define.py` — the kind of thing that works until someone
moves a line. So the rows here are plain tuples and `define.py` constructs the `Term` objects.

It also keeps the file honest about what it is: **content, not logic**. Everything structural
is enforced elsewhere, by `tools/verify_define.py`.

## The rules every row obeys, all machine-checked

- **Two sentences, 40 words maximum.** Piper reads at ~160 words per minute, so 40 words is
  about 15 seconds, and a definition is the answer to a small question.
- **Speakable.** `tau` not the Greek letter, `ohms` not the symbol, `times` not a star.
- **A reference, not a solver.** D43, on LB's own instruction — *"I am still planning to do my
  work."* Nothing here works a problem.
- **No bare ambiguous word.** This is the rule this file added, and it is load-bearing.
  `phase`, `power`, `ground`, `load`, `gate` and `field` mean different things in different
  courses, and `look_up` matches longest-first across ONE flat table — so the first course to
  claim a word claims it for every course. `electrical ground`, never bare `ground`.
  Where a bare word is already held by a math or physics entry, that entry keeps it.

## Provenance

The `circuits` rows below were written by hand on 2026-08-15 — the foundational terms, where
the definition is not in dispute, seeded deliberately so that the integration path was proven
by content nobody has to second-guess.

The remaining subjects come from `training/ee_encyclopedia.ipynb`, which drafts each entry by
**compressing a fetched source passage** rather than recalling from model weights, adjudicates
it against a second model from a different family, and mines what `tiny.en` actually hears for
each term. Its output is a review queue; `media/data/*-ee-provenance.csv` records the source
of every entry that ships.
"""

from __future__ import annotations

# Each row is (key, subject, spellings, spoken) — the positional shape of define.Term.
#
# `spellings` is every way the transcript might carry the term, matched as whole phrases
# against already-normalised text. STT is `tiny.en` and it mishears technical vocabulary, so
# a spelling list that only contains the correct word is a list that only works when the
# microphone does. Section 7 of the notebook measures the real mishearings; the hand-written
# rows below carry the obvious ones.

EE_ROWS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (

    # ================================================================== EEGR 202/203 — circuits
    # The foundations first. That "what is current" and "what is resistance" returned nothing
    # at all, to an EE major, is the headline of D47.

    ("electric_current", "circuits", ("electric current", "current"),
     "Current is the flow of electric charge past a point, measured in amperes. "
     "One amp is one coulomb of charge going by every second."),

    # "electric potential" and "potential difference" are NOT claimed here: physics_em's
    # `electric_potential` already holds them and its answer serves an EE student fine. The
    # duplicate-alias guard in define._alias_order() rejected the grab on first import, which
    # is the invariant working exactly as intended.
    ("voltage", "circuits", ("voltage",),
     "Voltage is the difference in electrical potential between two points, measured in volts. "
     "It is the push; current is the flow that results from it."),

    ("electrical_resistance", "circuits", ("electrical resistance", "resistance"),
     "Resistance is how strongly something opposes current, measured in ohms. "
     "One ohm lets one amp through for every volt you put across it."),

    ("conductance", "circuits", ("conductance",),
     "Conductance is the reciprocal of resistance, measured in siemens. "
     "High conductance means current passes easily."),

    ("capacitance", "circuits", ("capacitance",),
     "Capacitance is how much charge a component holds per volt across it, measured in farads. "
     "A capacitor opposes a change in voltage."),

    ("impedance", "circuits", ("impedance",),
     "Impedance is resistance generalised to alternating current, measured in ohms. "
     "It combines plain resistance with reactance, so it has a size and a phase angle."),

    ("reactance", "circuits", ("reactance",),
     "Reactance is the part of impedance contributed by capacitors and inductors rather than "
     "resistors. It changes with frequency, and it pushes current out of phase with voltage."),

    ("admittance", "circuits", ("admittance",),
     "Admittance is the reciprocal of impedance, measured in siemens. "
     "It says how easily alternating current flows, where impedance says how hard it is opposed."),

    ("thevenin_equivalent", "circuits",
     ("thevenin equivalent", "thevenins theorem", "thevenin theorem", "thevenin"),
     "Any linear circuit, seen from two terminals, behaves like one voltage source in series "
     "with one resistance. That pair is its Thevenin equivalent."),

    ("norton_equivalent", "circuits",
     ("norton equivalent", "nortons theorem", "norton theorem"),
     "The Norton equivalent is Thevenin's idea with a current source in parallel with a "
     "resistance instead. The two forms convert into each other through Ohm's law."),

    ("nodal_analysis", "circuits", ("nodal analysis", "node voltage method"),
     "Nodal analysis solves a circuit by applying Kirchhoff's current law at every node and "
     "solving for the node voltages. It is the method that scales to big circuits."),

    ("mesh_analysis", "circuits", ("mesh analysis", "mesh current method", "loop analysis"),
     "Mesh analysis solves a circuit by applying Kirchhoff's voltage law around every loop and "
     "solving for the loop currents. It is nodal analysis seen from the other side."),

    ("electrical_resonance", "circuits",
     ("electrical resonance", "resonant circuit", "resonant frequency of a circuit"),
     "Resonance is the frequency where capacitive and inductive reactance cancel, leaving only "
     "resistance. A series circuit draws its largest current there, a parallel one its smallest."),

    ("quality_factor", "circuits", ("quality factor", "q factor"),
     "The quality factor, Q, is how sharp a resonance is: centre frequency divided by bandwidth. "
     "A high Q means a narrow, selective peak and low losses."),

    ("short_circuit", "circuits", ("short circuit",),
     "A short circuit is an unintended low resistance path that lets current bypass the load. "
     "Current climbs until something stops it, usually a fuse."),

    ("open_circuit", "circuits", ("open circuit",),
     "An open circuit is a break in the path, so no current flows at all. "
     "The full source voltage appears across the break."),

    ("electrical_ground", "circuits", ("electrical ground", "circuit ground", "ground reference"),
     "Ground is the reference point every other voltage is measured against. "
     "It is a choice, not a physical fact, and moving it changes every number in the circuit."),

    ("series_circuit", "circuits", ("series circuit", "components in series"),
     "In series, components share one path, so the same current runs through all of them and "
     "their voltages add. Break it anywhere and everything stops."),

    ("parallel_circuit", "circuits", ("parallel circuit", "components in parallel"),
     "In parallel, components share the same two nodes, so they all see the same voltage and "
     "their currents add. One branch can fail with the rest still running."),

    # bare "transient" belongs to diffeq's eigenfunction entry — see the note on `voltage`.
    ("transient_response", "circuits", ("transient response",),
     "The transient is what a circuit does in the moments after a change, before it settles. "
     "It dies away, and what is left behind is the steady state."),

    # The homonym rule in practice. physics_mechanics keeps bare "power" — it got there first
    # and "the rate of doing work" is not wrong — so the EE sense arrives as a qualified
    # phrase. "What is power" still answers mechanics; "what is electrical power" answers this.
    ("electrical_power", "circuits", ("electrical power", "electric power"),
     "Electrical power is voltage times current, measured in watts. "
     "In a resistor it is also current squared times resistance, "
     "which is why doubling the current quadruples the heat."),

    ("electrical_load", "circuits", ("electrical load", "circuit load", "load"),
     "The load is whatever draws power from the source, whether a motor, a lamp or a resistor. "
     "Its impedance sets how much current the source has to deliver."),

    ("source_transformation", "circuits", ("source transformation",),
     "A voltage source in series with a resistance can be swapped for a current source in "
     "parallel with the same resistance. The rest of the circuit cannot tell the difference."),

    ("maximum_power_transfer", "circuits",
     ("maximum power transfer", "maximum power transfer theorem"),
     "A source delivers the most power to a load when the load resistance equals the source "
     "resistance. That point is only fifty percent efficient, so power systems avoid it."),

    ("ac_versus_dc", "circuits", ("alternating current", "direct current"),
     "Direct current flows one way at a steady level; alternating current reverses direction "
     "many times a second. Mains is alternating, a battery is direct."),

    # ============================================================ EEGR — electronics
    # Drafted 2026-08-15 by tools/draft_ee_entries.py: each entry is a COMPRESSION of a
    # fetched Wikipedia passage, entailment-gated against that passage, never recalled from
    # model weights. Source URL and revision per entry in media/data/ee-provenance.csv.

    ('bipolar_junction_transistor', 'electronics', ('bipolar junction transistor', 'bjt'),
     'A bipolar junction transistor uses both electrons and holes as charge carriers. A small current at one terminal controls a larger current, allowing for amplification or switching.'),

    ('clipping', 'electronics', ('clipping',),
     'Clipping is distortion that limits a signal once it passes a threshold. It can be hard with a flat cutoff or soft with reduced gain.'),

    ('common_emitter', 'electronics', ('common emitter',),
     'A common emitter is a single stage bipolar junction transistor amplifier topology typically used as a voltage amplifier. Its output is inverted and it offers high current gain with medium input resistance.'),

    ('comparator', 'electronics', ('comparator',),
     'A comparator is a device that compares two voltages or currents and outputs a digital signal showing which one is larger. It uses a specialized high-gain differential amplifier and appears in analog to digital converters.'),

    ('field_effect_transistor', 'electronics', ('field effect transistor',),
     'A field effect transistor uses an electric field to control current through a semiconductor. A voltage applied to the gate alters the conductivity between the drain and source.'),

    ('input_impedance', 'electronics', ('input impedance',),
     'Input impedance is the measure of opposition to current entering a load network. It includes both static resistance and dynamic reactance.'),

    ('light_emitting_diode', 'electronics', ('light emitting diode',),
     'A light emitting diode is a semiconductor component that emits light when current flows through it. Electrons recombine with holes to release energy as photons.'),

    ('mosfet', 'electronics', ('mosfet',),
     'A mosfet is a type of field effect transistor with an insulated gate. Its gate voltage controls the conductivity of the device, which allows it to amplify or switch electronic signals.'),

    ('negative_feedback', 'electronics', ('negative feedback',),
     'Negative feedback is when an amplifier subtracts a fraction of its output from its input so it opposes the original signal. This opposes the signal to improve performance and reduce sensitivity to parameter variations.'),

    ('operational_amplifier', 'electronics', ('operational amplifier', 'op amp', 'opamp'),
     'An operational amplifier is a direct coupled electronic amplifier with differential inputs and extremely high gain. It amplifies the voltage difference between those inputs and is widely used in analog circuits.'),

    ('output_impedance', 'electronics', ('output impedance',),
     'Output impedance is the measure of opposition to current flow internal to an electrical source. It shows the source propensity to drop in voltage when a load draws current.'),

    ('push_pull_amplifier', 'electronics', ('push pull amplifier',),
     'A push pull amplifier uses a pair of active devices to alternately supply or absorb current from a load. This enhances load capacity, increases switching speed, and cancels even order harmonics to reduce distortion.'),

    ('rectifier', 'electronics', ('rectifier',),
     'A rectifier is a device that converts alternating current, which reverses direction, to direct current. It straightens the direction of current for power supplies and other uses.'),

    ('schmitt_trigger', 'electronics', ('schmitt trigger',),
     'A Schmitt trigger is an active comparator circuit that converts an analog input into a digital output using hysteresis. It uses positive feedback and dual thresholds to retain its value until the input changes enough.'),

    ('semiconductor_diode', 'electronics', ('semiconductor diode',),
     'A semiconductor diode is a crystalline piece of material with a p n junction connected to two terminals. It conducts electric current primarily in one direction.'),

    ('slew_rate', 'electronics', ('slew rate',),
     'Slew rate is the change of voltage or current per unit of time. In electronic circuits, limits on it guarantee proper signal transition speed and prevent errors.'),

    ('transistor_biasing', 'electronics', ('transistor biasing',),
     'Biasing is setting the steady direct current and voltage operating conditions for an electronic component that processes time varying signals. This bias provides the correct operating point when no input signal is applied.'),

    ('voltage_follower', 'electronics', ('voltage follower',),
     'A voltage follower is a unity gain amplifier that copies a signal between circuits. It transforms electrical impedance to protect the first circuit from the second load.'),

    ('voltage_gain', 'electronics', ('voltage gain',),
     'Voltage gain is the ratio of output voltage to input voltage for a circuit. It measures how much the circuit increases the signal amplitude.'),

    ('voltage_regulator', 'electronics', ('voltage regulator',),
     'A voltage regulator is a system designed to automatically maintain a constant voltage. It may use feed forward or negative feedback, and electromechanical or electronic parts.'),

    ('zener_diode', 'electronics', ('zener diode',),
     'A zener diode lets current flow backwards from anode to cathode once the voltage passes a threshold. These parts generate reference voltages and protect circuits from overvoltage.'),

    # ======================================================== EEGR — signals and systems

    ('band_pass_filter', 'signals', ('band pass filter',),
     'A bandpass filter passes frequencies within a certain range while attenuating frequencies outside that range. It is the inverse of a bandstop filter.'),

    ('bandwidth', 'signals', ('bandwidth',),
     'Bandwidth is the difference between the upper and lower frequencies in a continuous band. It is measured in hertz and helps determine a communication channel capacity.'),

    ('bode_plot', 'signals', ('bode plot',),
     'A bode plot is a graph of a system frequency response. It combines a magnitude plot in decibels with a phase plot.'),

    ('fast_fourier_transform', 'signals', ('fast fourier transform',),
     'A fast Fourier transform is an algorithm that quickly computes the discrete Fourier transform or its inverse. It converts a signal from its original domain to a frequency domain representation by factorizing the transform matrix.'),

    ('finite_impulse_response', 'signals', ('finite impulse response',),
     'A finite impulse response filter settles to zero in finite time. Its response to a finite length input has a finite duration.'),

    ('fourier_series', 'signals', ('fourier series',),
     'A Fourier series is the expansion of a periodic function into a sum of sines and cosines. It makes analyzing problems easier because trigonometric functions are well understood.'),

    ('fourier_transform', 'signals', ('fourier transform',),
     'The Fourier transform is an integral transform that takes a function and outputs another function showing which frequencies are present. It is like decomposing a musical chord into the intensities of its constituent pitches.'),

    ('frequency_response', 'signals', ('frequency response',),
     'Frequency response is the magnitude and phase of a system output as an input frequency function. It characterizes systems in the frequency domain, just like the impulse response characterizes systems in the time domain.'),

    ('group_delay', 'signals', ('group delay',),
     'Group delay describes the delay times experienced by a signal frequency component passing through a system. When these delays depend on frequency, the waveform experiences distortion.'),

    ('high_pass_filter', 'signals', ('high pass filter',),
     'A high pass filter passes signals with a frequency higher than a cutoff frequency. It attenuates frequencies lower than that cutoff.'),

    ('impulse_response', 'signals', ('impulse response',),
     'Impulse response is the output of a dynamic system when given a brief input signal. It describes the reaction of the system as a function of time.'),

    ('infinite_impulse_response', 'signals', ('infinite impulse response',),
     'Infinite impulse response means an impulse response continues indefinitely rather than dropping to zero after a certain time. Analog electronic filters built with resistors, capacitors, and inductors generally use this property.'),

    ('linear_time_invariant_system', 'signals', ('linear time invariant system',),
     'A linear time invariant system is one that produces an output signal subject to linearity and time invariance constraints. Its response to any input is found directly using convolution with the system impulse response.'),

    ('low_pass_filter', 'signals', ('low pass filter',),
     'A low pass filter passes signals with a frequency lower than a cutoff frequency and attenuates higher frequencies. It removes short term fluctuations and leaves the longer term trend.'),

    ('pole', 'signals', ('pole',),
     'A pole is a certain type of singularity of a complex valued function. It is the simplest type of non removable singularity of such a function.'),

    ('power_spectral_density', 'signals', ('power spectral density',),
     "Power spectral density describes how a signal's power is distributed into frequency components over time. It applies to signals lasting long enough to be treated as infinite."),

    ('step_response', 'signals', ('step response',),
     'Step response is how system outputs change over time when control inputs switch from zero to one. Knowing this helps you understand system stability and how it reaches a stationary state.'),

    ('transfer_function', 'signals', ('transfer function',),
     'A transfer function is a mathematical function that models a system output for each possible input. It is widely used in electronic engineering tools like circuit simulators and control systems.'),

    ('window_function', 'signals', ('window function',),
     'A window function is a zero valued mathematical function outside a chosen interval used for tapering. It distributes spectral leakage in different ways based on application needs.'),

    ('z_transform', 'signals', ('z transform',),
     'The z transform converts a discrete time signal into a complex valued frequency domain representation. It is considered a discrete time counterpart of the Laplace transform.'),

    # =================================================== EEGR — communications

    ('amplitude_modulation', 'communications', ('amplitude modulation',),
     "Amplitude modulation is a technique where a wave's instantaneous amplitude changes in proportion to a message signal. It is the earliest method used for transmitting audio in radio broadcasting."),

    ('baud_rate', 'communications', ('baud rate',),
     'Baud rate is the number of symbol changes or signaling events across a transmission medium per unit of time. It is measured in baud, which means symbols per second.'),

    ('bit_error_rate', 'communications', ('bit error rate',),
     'Bit error rate is the number of bit errors per unit time in a digital transmission. The errors happen when noise, interference, distortion, or synchronization errors alter the received bits.'),

    ('carrier_wave', 'communications', ('carrier wave',),
     'A carrier wave is a periodic waveform that conveys information through modulation. Its properties are modified by an information bearing signal to transmit data through space or share a medium.'),

    ('channel_coding', 'communications', ('channel coding',),
     'Channel coding controls transmission errors by encoding messages with extra redundant data. This lets the receiver detect and fix errors without needing a retransmission.'),

    ('companding', 'communications', ('companding',),
     'Companding is a method used to transmit signals with a large dynamic range over facilities with a smaller dynamic range. It compresses the signal at the transmitting end and expands it at the receiving end.'),

    ('frequency_modulation', 'communications', ('frequency modulation',),
     'Frequency modulation varies a carrier wave in step with a message signal amplitude. It is widely used in radio broadcasting because it rejects interference better than amplitude modulation.'),

    ('frequency_shift_keying', 'communications', ('frequency shift keying',),
     'Frequency shift keying is a modulation scheme that encodes digital data by shifting a carrier signal between discrete frequencies. It is used in communication systems like telemetry and caller ID.'),

    ('intersymbol_interference', 'communications', ('intersymbol interference',),
     'Intersymbol interference is signal distortion where one pulse spreads and interferes with subsequent symbols. This unwanted phenomenon causes errors at the receiver output, making communication less reliable.'),

    ('modulation_index', 'communications', ('modulation index',),
     'Modulation index describes how much the modulated variable of a carrier signal varies around its unmodulated level. It is defined differently in each modulation scheme.'),

    ('multiplexing', 'communications', ('multiplexing',),
     'Multiplexing combines multiple analog or digital signals into one signal over a transmission medium. This lets multiple users share a scarce physical resource.'),

    ('phase_modulation', 'communications', ('phase modulation',),
     'Phase modulation encodes a message as variations in the instantaneous phase of a carrier wave. It keeps the carrier frequency and amplitude constant while the phase changes to follow the message signal level.'),

    ('phase_shift_keying', 'communications', ('phase shift keying',),
     'Phase shift keying is a digital modulation process that conveys data by changing the phase of a carrier wave. It uses a finite number of phases to represent digital data.'),

    ('quadrature_amplitude_modulation', 'communications', ('quadrature amplitude modulation',),
     'Quadrature amplitude modulation conveys two independent signals by changing the amplitudes of two differently phased carrier waves. Both amplitude and phase are modulated to transmit information in telecommunications.'),

    ('shannon_capacity', 'communications', ('shannon capacity',),
     'Shannon capacity is the upper bound on error free information sent through a noisy channel with a certain bandwidth. It limits communication rates using signal power and Gaussian noise.'),

    ('signal_to_noise_ratio', 'communications', ('signal to noise ratio',),
     'Signal to noise ratio compares the level of a desired signal to background noise. It is the ratio of signal power to noise power, often expressed in decibels.'),

    # ================================================== EEGR — control systems

    ('bibo_stability', 'control', ('bibo stability',),
     'Bibo stability means a system will always produce a bounded output when given a bounded input. A bounded signal never exceeds a finite value at any time or step.'),

    ('closed_loop_control', 'control', ('closed loop control',),
     'Closed loop control uses a feedback controller to automatically manage a process. It compares the process variable with the desired setpoint and applies the difference as a control signal.'),

    ('controllability', 'control', ('controllability',),
     'Controllability means being able to steer a system around in its configuration space using certain admissible manipulations. It helps regulate states, stabilize unstable systems, and solve tracking problems.'),

    ('damping_ratio', 'control', ('damping ratio',),
     'The damping ratio is a dimensionless measure that characterises how damped an oscillating system is. It varies from zero for undamped systems up to greater than one for overdamped systems.'),

    ('natural_frequency', 'control', ('natural frequency',),
     'Natural frequency is the rate an oscillatory system oscillates without any disturbance. Resonance happens when a forced vibration matches this frequency.'),

    ('nyquist_plot', 'control', ('nyquist plot',),
     'A nyquist plot is a graphical technique for determining the stability of a linear dynamical system. It is used in electronics and control system engineering for analyzing systems with feedback.'),

    ('nyquist_stability_criterion', 'control', ('nyquist stability criterion',),
     'The Nyquist stability criterion is a graphical technique used to determine if a linear dynamical system is stable. It works by analyzing the open loop system without explicitly computing poles and zeros.'),

    ('open_loop_control', 'control', ('open loop control',),
     'Open loop control is a system where the input is independent of the process output. It does not use feedback to check if it reached the goal, so it cannot correct errors.'),

    ('overshoot', 'control', ('overshoot',),
     'Overshoot is when a signal goes past its target value. It happens especially in the step response of bandlimited systems like low pass filters.'),

    ('pid_controller', 'control', ('pid controller',),
     'A pid controller is a feedback mechanism that automatically adjusts machines and processes. It compares a target value with the actual value to apply corrective actions.'),

    ('rise_time', 'control', ('rise time',),
     'Rise time is the time a signal takes to change from a low value to a high value. It applies to both positive and negative step responses.'),

    ('root_locus', 'control', ('root locus',),
     'Root locus is a graphical method showing how system roots change when a parameter varies. It is used in control theory to determine stability.'),

    ('settling_time', 'control', ('settling time',),
     'Settling time is the time it takes for an output to enter and stay within a specified error band after a step input. It includes propagation delay, slewing, overload recovery, and final settling.'),

    ('state_space_representation', 'control', ('state space representation',),
     'A state space representation is a mathematical model tracking how inputs shape system behavior over time using differential equations. Its axes are state variables, and the system state is a vector.'),

    ('steady_state_error', 'control', ('steady state error',),
     'Steady state error is the lingering discrepancy that persists over time between the desired target value and the actual value of a system. The integral component of a controller considers past errors to eliminate it.'),

    # ==================================================== EEGR — digital logic

    ('analog_to_digital_converter', 'digital_logic', ('analog to digital converter',),
     'An analog to digital converter is a system that turns an analog signal into a digital signal. It outputs a binary number that is proportional to the input voltage or current.'),

    ('boolean_algebra', 'digital_logic', ('boolean algebra',),
     'Boolean algebra is a branch of algebra where variables are truth values like true and false. It describes logical operations using conjunction, disjunction, and negation instead of arithmetic.'),

    ('de_morgans_laws', 'digital_logic', ('de morgans laws',),
     "De Morgan's laws are transformation rules and valid rules of inference in propositional logic and Boolean algebra. They express conjunctions and disjunctions purely in terms of each other via negation."),

    ('decoder', 'digital_logic', ('decoder',),
     'A decoder is a logic circuit that converts binary input signals into unique output signals. It maps every unique input combination to a specific combination of output states.'),

    ('digital_to_analog_converter', 'digital_logic', ('digital to analog converter',),
     'A digital-to-analog converter is a system that turns a digital signal into an analog signal. These are commonly used in music players and televisions.'),

    ('exclusive_or', 'digital_logic', ('exclusive or', 'xor', 'x or'),
     'Exclusive or is a logical operator that is true only when its inputs differ. It excludes the case where both inputs are true.'),

    ('flip_flop', 'digital_logic', ('flip flop',),
     'A flip flop is an edge triggered circuit with two stable states that stores a single bit of data. It is a fundamental building block of digital electronics and sequential logic systems.'),

    ('karnaugh_map', 'digital_logic', ('karnaugh map',),
     'A karnaugh map is a diagram used to simplify a boolean algebra expression. It remains relevant in the digital age for logical circuit design.'),

    ('latch', 'digital_logic', ('latch',),
     'A latch is a level-triggered circuit that stores a single bit of data. When enabled, it becomes transparent and outputs its state based on control inputs.'),

    ('logic_gate', 'digital_logic', ('logic gate',),
     'A logic gate is a device that performs a Boolean function on binary inputs to produce a single binary output. Most gates are made from transistors acting as electronic switches.'),

    ('multiplexer', 'digital_logic', ('multiplexer',),
     'A multiplexer is a device that selects one of several input signals and forwards it to a single output line. Select lines direct this choice, allowing multiple inputs to share a single resource.'),

    ('propagation_delay', 'digital_logic', ('propagation delay',),
     'Propagation delay is the time duration taken for a signal to reach its destination. It applies to electromagnetic fields, wires, gases, fluids, or solid bodies.'),

    ('shift_register', 'digital_logic', ('shift register',),
     'A shift register is a digital circuit of cascaded flip flops sharing a clock signal that moves stored data from one location to the next. They can have serial and parallel inputs and outputs for various data configurations.'),

    ('truth_table', 'digital_logic', ('truth table',),
     'A truth table is a mathematical table that sets out the functional values of logical expressions for every combination of input variables. Each row shows one possible configuration of inputs and the resulting value of the operation.'),

    ('twos_complement', 'digital_logic', ('twos complement',),
     'Twos complement is the most common method of representing signed integers on computers. It uses the most significant bit as the sign and has only one representation for zero.'),

    # ========================== EEGR — electromagnetics and transmission lines

    ('amperes_law', 'electromagnetics', ('amperes law',),
     'Amperes law relates the circulation of a magnetic field around a closed loop to the electric current passing through it. Maxwell generalized this law by adding the displacement current term.'),

    ('antenna', 'electromagnetics', ('antenna',),
     'An antenna is a structure that converts alternating electric currents into radio waves and back again. It acts as the interface between space and metal conductors in radio equipment.'),

    ('characteristic_impedance', 'electromagnetics', ('characteristic impedance',),
     'Characteristic impedance is the ratio of voltage to current for a wave moving one way on a line without reflections. It is measured in ohms and depends on the line geometry and materials.'),

    ('coaxial_cable', 'electromagnetics', ('coaxial cable',),
     'Coaxial cable is an unbalanced transmission line that carries high frequency electrical signals with low losses. It features an inner conductor and an outer shield sharing a geometric axis.'),

    ('eddy_current', 'electromagnetics', ('eddy current',),
     'An eddy current is a loop of electric current induced inside conductors by a changing magnetic field or motion. These circular currents flow in closed planes and oppose the field change that created them.'),

    ('impedance_matching', 'electromagnetics', ('impedance matching',),
     'Impedance matching is adjusting a device input or output impedance to a desired value. This is done to maximize power transfer or minimize signal reflection.'),

    ('insertion_loss', 'electromagnetics', ('insertion loss',),
     'Insertion loss is the loss of signal power that happens when you put a device in a transmission line or optical fiber. It is usually expressed in decibels.'),

    ('magnetic_hysteresis', 'electromagnetics', ('magnetic hysteresis',),
     'Magnetic hysteresis is when a magnetic system depends on its history rather than just the current field. This rate independent effect creates durable memory loops in ferromagnets.'),

    ('microstrip', 'electromagnetics', ('microstrip',),
     'Microstrip is a planar electrical transmission line formed by a conductor separated from a ground plane by a dielectric substrate. It conveys microwave frequency signals and high speed digital signals across printed circuit boards.'),

    ('phase_velocity', 'electromagnetics', ('phase velocity',),
     'Phase velocity is the speed at which any wavefront of constant phase travels. This speed for a wave component is not a physically meaningful quantity and does not relate to information transfer.'),

    ('poynting_vector', 'electromagnetics', ('poynting vector',),
     'The poynting vector represents the directional energy flux or power flow of an electromagnetic field. Its unit is the watt per square metre.'),

    ('propagation_constant', 'electromagnetics', ('propagation constant',),
     'Propagation constant measures the change in amplitude and phase of a wave per unit length. Because phase varies with distance, this logarithmic value is a complex number.'),

    ('reflection_coefficient', 'electromagnetics', ('reflection coefficient',),
     'The reflection coefficient describes how much of a wave is reflected by an impedance discontinuity in a transmission medium. It equals the ratio of the amplitude of the reflected wave to the incident wave.'),

    ('return_loss', 'electromagnetics', ('return loss',),
     'Return loss is a relative measure of the signal power reflected by a discontinuity in a line or fiber. It shows how well devices are matched, and a high return loss is desirable.'),

    ('skin_effect', 'electromagnetics', ('skin effect',),
     'Skin effect is the tendency of alternating current to flow mostly near the surface of a conductor. It is caused by opposing eddy currents and reduces the effective cross section.'),

    ('smith_chart', 'electromagnetics', ('smith chart',),
     'The Smith chart is a circular nomogram used in radio frequency engineering to solve transmission line problems. It plots a complex reflection coefficient on a grid of normalized electrical impedance.'),

    ('standing_wave_ratio', 'electromagnetics', ('standing wave ratio',),
     'Standing wave ratio measures impedance matching of loads to a transmission line. It is the ratio of the maximum amplitude to the minimum amplitude along the line.'),

    ('transmission_line', 'electromagnetics', ('transmission line',),
     'A transmission line is a specialized structure designed to conduct electromagnetic waves in a contained manner. The term applies when conductors are long enough that the wave nature of the transmission must be considered.'),

    # ===================================== EEGR — microelectronics and devices

    ('band_gap', 'microelectronics', ('band gap',),
     'A band gap is an energy range in a solid where no electronic states exist. It is the energy required to promote an electron from the valence band to the conduction band.'),

    ('cmos', 'microelectronics', ('cmos',),
     'Cmos is a fabrication process using complementary pairs of transistors for logic functions. It has high noise immunity and low static power consumption.'),

    ('conduction_band', 'microelectronics', ('conduction band',),
     'The conduction band is the lowest range of vacant electronic states in a solid. It is located above the Fermi level in semiconductors and nonmetals.'),

    ('depletion_region', 'microelectronics', ('depletion region',),
     'The depletion region is an insulating zone inside a doped semiconductor where mobile charge carriers have been pushed away. It contains only ionized impurities and leaves no carriers behind to carry a current.'),

    ('doping', 'microelectronics', ('doping',),
     'Doping is adding impurities to a semiconductor to change its electrical properties. The resulting material is called an extrinsic semiconductor.'),

    ('intrinsic_semiconductor', 'microelectronics', ('intrinsic semiconductor',),
     'An intrinsic semiconductor is a pure material without significant dopants, so its charge carriers depend on the material itself. The number of excited electrons equals the number of holes, enabling current through band gap excitation.'),

    ('majority_carrier', 'microelectronics', ('majority carrier',),
     'A charge carrier is a free particle or quasiparticle that carries an electric charge through a conductor. Examples of these free particles include electrons, ions, and holes.'),

    ('photolithography', 'microelectronics', ('photolithography',),
     'Photolithography uses light to transfer a pattern onto a photoresist layer on a silicon wafer. It is the most common method for semiconductor fabrication of integrated circuits.'),

    ('pn_junction', 'microelectronics', ('pn junction',),
     'A p n junction combines p-type and n-type semiconductor materials in a single crystal. This creates a depletion region that allows current to pass through in only one direction.'),

    ('semiconductor', 'microelectronics', ('semiconductor',),
     'A semiconductor is a material whose electrical conductivity sits between that of a conductor and an insulator. Its conductivity changes through doping or temperature, forming the basis of modern electronics.'),

    ('threshold_voltage', 'microelectronics', ('threshold voltage',),
     'Threshold voltage is the minimum gate to source voltage needed to make a conducting path between the source and drain terminals. It is an unambiguous scaling factor for keeping power efficiency in any field effect transistor.'),

    ('valence_band', 'microelectronics', ('valence band',),
     'The valence band is the highest range of electron energies with electrons present at absolute zero temperature. It lies below the Fermi level and helps determine conductivity.'),

    ('wafer', 'microelectronics', ('wafer',),
     'A wafer is a thin slice of semiconductor used to build integrated circuits and solar cells. It serves as the substrate for various microelectronic devices.'),

    # ==================================================== EEGR — power systems

    ('apparent_power', 'power_systems', ('apparent power',),
     'Apparent power is not defined in the source passage. The source passage only discusses instantaneous, active, real, and reactive power.'),

    ('bus_bar', 'power_systems', ('bus bar',),
     'A bus bar is a metallic strip used for local high current power distribution, transmission, or switching. They are generally uninsulated and stiff enough to be supported in air by insulated pillars.'),

    ('circuit_breaker', 'power_systems', ('circuit breaker',),
     'A circuit breaker is a safety device that stops current flow to protect equipment from overcurrent damage and prevent fires. Unlike a fuse, it can be reset to resume normal operation.'),

    ('delta_connection', 'power_systems', ('delta connection',),
     'A delta connection is a circuit diagram shape that looks like the capital letter delta. It is used in the analysis of three phase electric power circuits.'),

    ('harmonics', 'power_systems', ('harmonics',),
     'A harmonic is a sinusoidal wave whose frequency is an integer multiple of the fundamental frequency. They are caused by non-linear loads and create power quality problems.'),

    ('induction_motor', 'power_systems', ('induction motor',),
     'An induction motor is an alternating current motor where rotor current comes from electromagnetic induction. It needs no electrical connections to the rotor.'),

    ('load_flow', 'power_systems', ('load flow',),
     'Load flow is a numerical analysis of electric power flowing in an interconnected system during normal steady state operation. It gives voltage magnitudes and phase angles at each bus, plus real and reactive power in each line.'),

    ('per_unit_system', 'power_systems', ('per unit system',),
     'A per unit system expresses power system quantities as fractions of a defined base unit. This simplifies calculations because these values do not change across transformers.'),

    ('power_factor_correction', 'power_systems', ('power factor correction',),
     'Power factor correction increases the power factor of a load. This improves the efficiency for the distribution system it is attached to.'),

    ('reactive_power', 'power_systems', ('reactive power',),
     'Reactive power is the portion of power that oscillates between the source and load each cycle without net energy transfer. Its amplitude is the absolute value of this alternating power.'),

    ('synchronous_machine', 'power_systems', ('synchronous machine',),
     'A synchronous machine is an alternating current device that includes synchronous motors and generators. In these machines, the rotor turns in step with the rotating magnetic field of the stator.'),

    ('three_phase_power', 'power_systems', ('three phase power',),
     'Three phase power is the most widely used form of alternating current for electricity generation, transmission, and distribution. It uses three wires offset by one hundred twenty degrees to produce a constant flow of power.'),

    ('transformer', 'power_systems', ('transformer',),
     'A transformer is a passive component that transfers electrical energy between circuits without a metallic connection. A varying current in one coil produces a changing magnetic flux in the core that induces an electromotive force across other coils.'),

    ('voltage_regulation', 'power_systems', ('voltage regulation',),
     'Voltage regulation measures the change in voltage magnitude between the sending and receiving ends of a line. It describes a system ability to provide near constant voltage over varied load conditions.'),

    ('wye_connection', 'power_systems', ('wye connection',),
     'A wye connection is part of a circuit transformation used to simplify network analysis. Its name comes from the shape of the circuit diagram, which looks like the letter.'),
)

# Analog-factory — Project Development Prompt

You are a technical architect with expertise in analog integrated circuit design, open-source EDA, desktop application development, LLM agents, and reinforcement learning systems.

Help me design and implement an open-source project called Analog-factory in phases.

## 1. Project Vision and Core Principles

Analog-factory is a local-first workspace for analog integrated circuit design on Ubuntu. It provides four progressively integrated capabilities:

1. A complete manual analog IC design and verification workflow.
2. A built-in conversational and design-assistance agent that understands project context.
3. Visual tools for users to build, debug, and reuse their own analog circuit design agents.
4. Agent evaluation and reinforcement learning using real EDA environments.

**Project priorities:** Complete manual design workflow > Reliable toolchain and data > AI assistance > Agent orchestration > RL training.

**Core requirements:**

- Manual design functionality must remain fully usable when AI is disabled, no API key is configured, or no GPU is available.
- Manual operations and agent operations must share the same EDA tool interfaces, project data, and task records.
- Reuse mature open-source tools. Focus development on integration, interaction, automation, and reproducibility.
- All simulation, DRC, LVS, and training results must come from actual execution.
- Clearly distinguish implemented, experimental, planned, and currently unverifiable functionality.

## 2. Platform and EDA Toolchain

Target Ubuntu LTS. Select one primary supported version first, document desktop environment requirements, dependencies, and hardware requirements, and provide a reproducible installation method.

**Core tools:**

- **xschem:** schematics, symbols, hierarchical design, and netlist generation.
- **ngspice:** circuit simulation, parameter sweeps, and measurements.
- **magic:** layout editing, DRC, netlist extraction, and parasitic extraction.
- **netgen:** layout-versus-schematic verification.
- **SKY130 PDK:** devices, models, process rules, and tool configurations.

Provide a unified environment management interface to:

- Detect tool paths, versions, availability, and missing dependencies.
- Configure `PDK_ROOT`, PDK versions, process libraries, and model paths.
- Manage project-specific configurations for xschem, magic, ngspice, and netgen.
- Pin validated combinations of tool and PDK versions.
- Provide diagnostic logs and actionable repair suggestions.

Investigate open_pdks or another reliable PDK distribution method. Distinguish raw PDK source files from usable installations prepared for the individual EDA tools.

Validate native GUI integration before committing to an implementation:

- Compare coordinated external windows, desktop integration, and window embedding.
- Explain how X11, Wayland, and desktop application frameworks affect integration.
- The MVP may launch native xschem and magic windows, but project context, files, task status, and results must remain coordinated.
- Treat deeper embedding as a separate enhancement so it does not block the complete design workflow.

## 3. Complete Manual Analog IC Design Workflow

Users must be able to complete the following workflow through the workspace:

```text
Project creation
→ Specification definition
→ Schematic design
→ Testbench configuration
→ Pre-layout simulation
→ Layout design
→ DRC
→ LVS
→ Parasitic extraction
→ Post-layout simulation
→ Comparison of pre-layout and post-layout results
→ Result archival and design file export.
```

### 3.1 Project Management

Support project templates, design hierarchy, file browsing, revision history, and organized management of design source files, testbenches, run artifacts, and reports.

### 3.2 Simulation Configuration

Provide graphical configuration for operating point, DC, AC, transient, noise, parameter sweeps, and PVT analysis, while retaining direct editing of SPICE directives.

Enable Monte Carlo and similar capabilities only when supported by the PDK models and simulator. Clearly distinguish process corners, global statistical variation, and device mismatch.

### 3.3 Waveforms and Measurements

Support waveform zooming, cursors, multiple-trace comparison, unit display, and data export.

Allow users to configure measurements such as gain, bandwidth, phase margin, power consumption, and output swing.

Every metric must be associated with its test conditions, measurement definition, raw output, and execution record.

### 3.4 Layout and Verification

Launch and manage layout editing in magic, and execute DRC, LVS, and extraction tasks.

Display detailed errors and, where supported by the tools, navigate to the relevant objects or locations.

Clearly document the coverage and limitations of parasitic extraction. Do not describe unverified extraction results as complete RC signoff.

### 3.5 Reproducible Execution

For every run, record input snapshots, file hashes, tool versions, PDK version, command arguments, execution environment, logs, exit status, and output artifacts.

When a design changes, mark affected previous results as stale to prevent accidental reuse.

Support cancellation, timeouts, and retries. Each retry must create a new execution record.

## 4. Built-in Conversational and Design-Assistance Agent

Use Netcatty as a reference for agent switching, terminal collaboration, and context-aware interaction. Design a unified AI sidebar.

Support two integration categories:

### A. Model APIs

Connect to user-configured cloud APIs or local model services. Analog-factory’s Agent Runtime manages context, tool calls, and task execution.

### B. External Agent CLIs

Detect locally installed tools such as Codex CLI and Claude Code CLI. Integrate them through their publicly supported protocols, SDKs, or non-interactive interfaces.

Provide an interactive terminal fallback where appropriate for the tool’s actual capabilities.

Do not assume these two integration categories have identical capabilities:

- Maintain a capability matrix covering streaming output, tool calls, session resumption, cancellation, file access, usage reporting, and other relevant features.
- Prefer structured events over fragile terminal text parsing.
- Use each CLI’s official authentication and authorization mechanisms.
- Do not access or copy credentials without authorization or modify the user’s existing global configuration.
- Display unavailable usage or cost information as unknown rather than inventing values.

The agent should be able to:

- Answer questions about analog circuits, tool usage, and the current project.
- Read user-selected schematics, netlists, logs, waveforms, and verification reports.
- Explain simulation failures, convergence problems, and DRC/LVS errors.
- Propose design changes and generate testbenches, measurement scripts, and sweep configurations.
- Execute design and verification tasks within the scope authorized by the user.

**Interaction requirements:**

- Clearly display the active model, agent backend, and files included in context.
- Distinguish suggestions, pending actions, and verified results.
- Provide diff review and rollback for file changes.
- Support read-only assistance, execution after confirmation, and automatic execution within a defined scope.
- Allow the user to stop a task and take control at any time.
- Link important conclusions to the relevant files, logs, or execution results.

## 5. Visual Model and API Configuration

Provide a complete configuration interface supporting:

- Provider name, protocol type, Base URL, API key, and model name.
- Local model services and custom compatible endpoints.
- Model discovery, with manual model entry when discovery is unsupported.
- Connection testing, timeouts, retries, concurrency, and generation parameters supported by the selected model.
- Global defaults and project-specific overrides.
- Different models for different agents or workflow nodes.
- Configuration import and export, excluding secrets by default.
- Secure credential storage and redaction in the interface and logs.

Do not treat all providers as offering fully compatible APIs.

Use Provider Adapters to handle protocol and capability differences.

## 6. Visual Agent Builder

Provide a node-and-edge editor that allows users to build, debug, save, and reuse analog circuit design agents.

Include at least the following node types:

- User input and design specifications.
- Model inference.
- Project knowledge retrieval.
- Schematic or netlist processing.
- ngspice simulation.
- Waveform analysis and metric extraction.
- DRC, LVS, and parasitic extraction.
- Conditional branches, loops, and parameter searches.
- Human review checkpoints.
- Report and artifact generation.

**Execution capabilities:**

- Define typed node inputs and outputs, including variable mappings.
- Support branching, bounded loops, retries, timeouts, and failure handling.
- Provide node status, live logs, step-by-step debugging, and execution history.
- Apply iteration limits, runtime limits, and cost budgets to loops.
- Clearly distinguish inspecting historical executions from rerunning them. Cached results must be identifiable.
- Prevent multiple nodes from simultaneously overwriting the same design source files.

Workflows must be exportable as versioned JSON/YAML and executable by an independent execution engine, decoupling the GUI from execution logic.

Include example agents:

1. Simulation failure diagnosis agent.
2. Circuit parameter sweep and tuning agent.
3. Pre-layout versus post-layout comparison agent.
4. DRC/LVS result analysis agent.

Provide at least one real closed-loop workflow:

```text
Read specifications
→ Adjust parameters
→ Run simulation
→ Extract metrics
→ Check whether specifications are met
→ Continue iterating or generate a report.
```

## 7. Agent Evaluation and Reinforcement Learning

Provide a pluggable Agent RL integration layer. Evaluate candidate frameworks such as Agent Lightning, AReaL, and AgentFly.

Verify the following during framework selection:

- Current version, maintenance status, and license.
- Agent interfaces, training backends, and dependency relationships.
- Supported algorithms, models, and hardware requirements.
- Support for multi-turn tool use, trajectory recording, and asynchronous simulation.
- Integration effort required to connect the framework to the project’s execution engine.

Integrate multiple frameworks in phases, starting with one real end-to-end training workflow.

Clearly label the integration status of other frameworks. An empty configuration page does not count as a completed integration.

Strictly distinguish:

1. Circuit parameter optimization.
2. Agent workflow, prompt, or strategy optimization.
3. Reinforcement learning that updates trainable model weights.

Treat ordinary inference APIs and authenticated agent CLIs as execution or evaluation backends by default.

Only services with explicit training interfaces or trainable models may participate in the corresponding training workflows.

Build a unified Analog Design Environment:

- **Observation:** circuit state, specifications, parameters, simulation metrics, and historical results.
- **Action:** modify parameters within controlled bounds, invoke tools, or select the next step.
- **Reward:** a computable result derived from real simulation and verification.
- **Termination:** specifications satisfied, failure, resource exhaustion, or step limit reached.

**Environment requirements:**

- Give each episode an isolated working directory, with reset, tracing, and reproducibility support.
- Calculate rewards using an independent evaluator. The agent must not modify evaluation specifications or scoring code.
- Record hard constraints, performance objectives, failure states, and computational costs separately.
- Account for metric normalization, PVT robustness, and actual compute budgets in reward design.
- Handle non-convergence, timeouts, missing measurements, invalid circuits, and verification failures.
- Preserve the required trajectories, tool calls, inputs and outputs, reward breakdowns, and data provenance.
- Preserve token-level and other information required by the selected training framework. Do not assume ordinary chat logs are sufficient for training.

Provide a training interface covering task sets, frameworks, models, rewards, resources, run status, training curves, checkpoints, and evaluation reports.

Start by validating the environment with a current mirror or simple amplifier task that has a manageable number of parameters. Then expand to more complex tasks such as OTAs.

Evaluate performance using held-out tasks and baselines such as random search.

When no GPU is available, retain trajectory collection, replay, and evaluation capabilities, and clearly explain why training is unavailable.

## 8. Architecture and User Interface

Recommend a technology stack and explain how it supports local process management, native GUI coordination, node editing, waveform visualization, and the Python training ecosystem.

Include at least the following modules:

- Workspace UI.
- Project and artifact management.
- EDA Tool Adapters.
- Task execution and resource management.
- Provider and CLI Adapters.
- Agent Runtime.
- Visual workflow execution engine.
- Evaluation and RL Adapters.

Prefer a modular monolith with separate task processes for the MVP.

Install and isolate training dependencies on demand so they do not burden the core EDA workspace.

The interface should include a project tree, central workspace, AI sidebar, terminal and log area, and dedicated views for simulation, verification, agent building, training, and settings.

Task status, errors, input provenance, and result validity must be clearly visible.

Prioritize complete end-to-end usability before adding visual decoration and advanced features.

## 9. Phased Delivery and Acceptance Criteria

### Phase 0: Technical Validation

Validate the toolchain, PDK, GUI coordination, and critical interfaces.

Deliver a compatibility matrix, architecture proposal, and validation results for the main technical risks.

### Phase 1: Manual Design MVP

Use a simple analog circuit to complete the full workflow: schematic, pre-layout simulation, layout, DRC, LVS, extraction, and post-layout simulation.

All operations must remain available with AI disabled.

### Phase 2: AI Assistant

Connect one model API and establish usable integration paths for Codex CLI and Claude Code CLI.

Implement context selection, tool calls, change review, and execution tracing.

### Phase 3: Visual Agents

Enable users to create, save, reload, and execute a parameter-tuning workflow driven by real simulations.

### Phase 4: Evaluation and RL

Complete a real training task using a trainable model, save checkpoints, and compare pre-training and post-training performance on held-out tasks.

Then extend support to additional frameworks.

**Each phase must deliver:**

- Runnable code and startup instructions.
- Reproducible examples.
- Tests for critical behavior and evidence from actual execution.
- A record of implemented functionality, known limitations, and the next phase’s plan.

DRC/LVS passes must be supported by actual reports. Exit codes or mock data must not substitute for verification results.

A working design flow and tapeout signoff represent different acceptance levels. Do not overstate verification conclusions.

## 10. Your First Response

First, provide:

1. An accurate restatement of the requirements and project scope.
2. An overall architecture diagram and key data flows.
3. A recommended technology stack and the reasoning behind major choices.
4. An integration matrix covering the toolchain, PDK, model APIs, CLIs, and RL frameworks.
5. A proposed repository structure and core data models.
6. A phased implementation plan with verifiable acceptance criteria.
7. The highest-priority technical validations and a concrete plan for the first end-to-end example.

Use reasonable defaults for routine, reversible technical decisions and explain the reasoning.

Request clarification only for questions that would materially change the architecture, cost, or project scope.

Do not substitute large amounts of placeholder UI code for real EDA integration. Proceed with implementation incrementally according to the phases above.

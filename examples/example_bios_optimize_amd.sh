# AMD EPYC performance / NUMA tuning. Attribute names vary by vendor —
# discover the exact ones your box uses before setting anything:
redfish_ctl bios-registry --attr_name Numa
# Common EPYC knobs (substitute the exact names bios-registry reports):
#   SMT (hyperthreading), NUMA nodes per socket (NPS), determinism, power profile
redfish_ctl bios-change --attr_name Smt                --attr_value Enabled           on-reset
redfish_ctl bios-change --attr_name NumaNodesPerSocket --attr_value NPS4              on-reset
redfish_ctl bios-change --attr_name DeterminismControl --attr_value Performance       on-reset
redfish_ctl bios-change --attr_name PowerProfileSelect --attr_value MaximumPerformance on-reset -r

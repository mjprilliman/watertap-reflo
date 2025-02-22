#################################################################################
# WaterTAP Copyright (c) 2020-2023, The Regents of the University of California,
# through Lawrence Berkeley National Laboratory, Oak Ridge National Laboratory,
# National Renewable Energy Laboratory, and National Energy Technology
# Laboratory (subject to receipt of any required approvals from the U.S. Dept.
# of Energy). All rights reserved.
#
# Please see the files COPYRIGHT.md and LICENSE.md for full copyright and license
# information, respectively. These files are also available online at the URL
# "https://github.com/watertap-org/watertap/"
#################################################################################

import pytest
import os

import pandas as pd
from pyomo.environ import (
    ConcreteModel,
    Var,
    value,
    assert_optimal_termination,
)
from pyomo.network import Port

from watertap_contrib.reflo.solar_models.surrogate.trough import TroughSurrogate
from watertap_contrib.reflo.solar_models.surrogate.trough.trough_surrogate import (
    TroughSurrogateData,
)
from watertap_contrib.reflo.costing import EnergyCosting
import idaes.logger as idaeslog
from idaes.core.util.testing import initialization_tester
from idaes.core.solvers import get_solver
from idaes.core.surrogate.pysmo_surrogate import PysmoSurrogate
from idaes.core.surrogate.surrogate_block import SurrogateBlock
from idaes.core import FlowsheetBlock, UnitModelCostingBlock
from idaes.core.util.model_statistics import (
    degrees_of_freedom,
    number_variables,
    number_total_constraints,
    number_unused_variables,
)
from idaes.core.util.scaling import (
    calculate_scaling_factors,
    unscaled_variables_generator,
)

# Get default solver for testing
solver = get_solver()

dataset_filename = os.path.join(os.path.dirname(__file__), "data/trough_data.pkl")
large_surrogate_filename = os.path.join(
    os.path.dirname(__file__), "trough_surrogate_100_500.json"
)
small_surrogate_filename = os.path.join(
    os.path.dirname(__file__), "trough_surrogate_10_100.json"
)


def get_data(heat_load_range):
    df = pd.read_pickle(dataset_filename)
    df = df[
        (df["heat_load"] >= heat_load_range[0])
        & (df["heat_load"] <= heat_load_range[1])
    ]
    return {"training": df.head(-10), "validation": df.tail(10)}


class TestTroughLarge:
    @pytest.fixture(scope="class")
    def trough_large_heat_load(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)
        m.fs.trough = TroughSurrogate(heat_load_range=[100, 500])
        return m

    @pytest.mark.unit
    @pytest.mark.skip
    def test_build_large(self, trough_large_heat_load):
        m = trough_large_heat_load

        assert len(m.fs.trough.config) == 4
        assert not m.fs.trough.config.dynamic
        assert not m.fs.trough.config.has_holdup
        assert m.fs.trough._tech_type == "trough"
        assert isinstance(m.fs.trough.surrogate_blk, SurrogateBlock)

        surr_input_str = ["heat_load", "hours_storage"]
        surr_output_str = ["heat_annual_scaled", "electricity_annual_scaled"]

        assert m.fs.trough.input_labels == surr_input_str
        assert m.fs.trough.surrogate.input_labels() == surr_input_str
        assert m.fs.trough.output_labels == surr_output_str
        assert m.fs.trough.surrogate.output_labels() == surr_output_str
        assert m.fs.trough.surrogate_file.lower() == large_surrogate_filename.lower()
        assert m.fs.trough.dataset_filename.lower() == dataset_filename.lower()
        assert m.fs.trough.surrogate.n_inputs() == 2
        assert m.fs.trough.surrogate.n_outputs() == 2

        for s in surr_input_str + surr_output_str:
            v = getattr(m.fs.trough, s)
            assert isinstance(v, Var)
        assert m.fs.trough.n_samples == 100
        assert m.fs.trough.training_fraction == 0.8

        no_ports = list()
        for c in m.fs.trough.component_objects():
            if isinstance(c, Port):
                no_ports.append(c)
        assert len(no_ports) == 0
        assert number_variables(m.fs.trough) == 6
        assert number_unused_variables(m.fs.trough) == 0
        assert number_total_constraints(m.fs.trough) == 4

    @pytest.mark.unit
    @pytest.mark.skip
    def test_surrogate_variable_bounds_large(self, trough_large_heat_load):
        m = trough_large_heat_load
        assert m.fs.trough.heat_load.bounds == tuple([100, 500])
        assert m.fs.trough.hours_storage.bounds == tuple([0, 26])

    @pytest.mark.component
    @pytest.mark.skip
    def test_create_rbf_surrogate_large(self, trough_large_heat_load):
        m = trough_large_heat_load
        data = get_data(m.fs.trough.heat_load.bounds)
        test_surrogate_filename = os.path.join(
            os.path.dirname(__file__), "test_surrogate.json"
        )
        m.fs.trough._create_rbf_surrogate(output_filename=test_surrogate_filename)

        assert (
            m.fs.trough.rbf_train.get_result("heat_annual_scaled").metrics["R2"] > 0.99
        )
        assert (
            m.fs.trough.rbf_train.get_result("heat_annual_scaled").metrics["RMSE"]
            < 0.005
        )
        assert (
            m.fs.trough.rbf_train.get_result("electricity_annual_scaled").metrics["R2"]
            > 0.99
        )
        assert (
            m.fs.trough.rbf_train.get_result("electricity_annual_scaled").metrics[
                "RMSE"
            ]
            < 0.005
        )

        assert os.path.getsize(test_surrogate_filename) > 0
        os.remove(test_surrogate_filename)
        assert isinstance(m.fs.trough.surrogate, PysmoSurrogate)
        test_output = m.fs.trough.surrogate.evaluate_surrogate(data["validation"])

        expected_heat_annual_test = data["validation"]["heat_annual"]
        expected_electricity_annual_test = data["validation"]["electricity_annual"]
        predicted_heat_annual = test_output["heat_annual_scaled"] / value(
            m.fs.trough.heat_annual_scaling
        )
        predicted_electricity_annual = test_output["electricity_annual_scaled"] / value(
            m.fs.trough.electricity_annual_scaling
        )

        tol = 1e-1
        assert list(predicted_heat_annual) == pytest.approx(
            expected_heat_annual_test.tolist(), tol
        )
        assert list(predicted_electricity_annual) == pytest.approx(
            expected_electricity_annual_test.tolist(), tol
        )

    @pytest.mark.component
    @pytest.mark.skip
    def test_validation_large(self, trough_large_heat_load):
        m = trough_large_heat_load
        data = get_data(m.fs.trough.heat_load.bounds)
        heat_annual_list = []
        electricity_annual_list = []

        expected_heat_annual = data["validation"]["heat_annual"]
        expected_electricity_annual = data["validation"]["electricity_annual"]

        for row in data["validation"].itertuples():
            m.fs.trough.heat_load.fix(row.heat_load)
            m.fs.trough.hours_storage.fix(row.hours_storage)
            m.fs.trough.initialize_build()
            results = solver.solve(m, tee=True)
            assert_optimal_termination(results)
            heat_annual_list.append(value(m.fs.trough.heat_annual))
            electricity_annual_list.append(value(m.fs.trough.electricity_annual))

        # ensure surrogate model gives same results when inside a flowsheet
        assert heat_annual_list == pytest.approx(expected_heat_annual, 5e-2)
        assert electricity_annual_list == pytest.approx(
            expected_electricity_annual, 0.1
        )

    @pytest.mark.unit
    @pytest.mark.skip
    def test_dof_large(self, trough_large_heat_load):
        m = trough_large_heat_load
        m.fs.trough.heat_load.fix(500)
        m.fs.trough.hours_storage.fix(12)
        assert degrees_of_freedom(m) == 0
        m.fs.trough.heat_load.unfix()
        m.fs.trough.hours_storage.unfix()
        assert degrees_of_freedom(m) == 2

    @pytest.mark.unit
    @pytest.mark.skip
    def test_calculate_scaling(self, trough_large_heat_load):
        m = trough_large_heat_load
        calculate_scaling_factors(m)
        assert len(list(unscaled_variables_generator(m))) == 0

    @pytest.mark.component
    @pytest.mark.skip
    def test_initialization(self, trough_large_heat_load):
        m = trough_large_heat_load
        m.fs.trough.heat_load.fix(200)
        m.fs.trough.hours_storage.fix(4)
        m.fs.trough.initialize()

    @pytest.mark.component
    @pytest.mark.skip
    def test_solve_large(self, trough_large_heat_load):
        results = solver.solve(trough_large_heat_load)
        assert_optimal_termination(results)

    @pytest.mark.component
    @pytest.mark.skip
    def test_solution_large(self, trough_large_heat_load):
        m = trough_large_heat_load

        trough_results = {
            "electricity": 727.242,
            "heat": 93885.264,
            "heat_load": 200.0,
            "hours_storage": 4.0,
            "heat_annual_scaled": 0.31056777,
            "electricity_annual_scaled": 0.048634913,
            "heat_annual": 822998231.988,
            "electricity_annual": 6375009.402,
        }

        for v, r in trough_results.items():
            tv = getattr(m.fs.trough, v)
            assert pytest.approx(r, rel=1e-1) == value(tv)

    @pytest.mark.component
    @pytest.mark.skip
    def test_costing_large(self, trough_large_heat_load):
        m = trough_large_heat_load
        m.fs.trough.initialize_build()
        m.fs.costing = EnergyCosting()

        m.fs.trough.costing = UnitModelCostingBlock(
            flowsheet_costing_block=m.fs.costing
        )
        m.fs.costing.factor_maintenance_labor_chemical.fix(0)
        m.fs.costing.factor_total_investment.fix(1)
        m.fs.costing.cost_process()

        results = solver.solve(m)
        assert_optimal_termination(results)

        sys_costing_dict = {
            "aggregate_capital_cost": 93537260.0,
            "aggregate_fixed_operating_cost": 1600000.0,
            "aggregate_variable_operating_cost": 823535.991,
            "aggregate_flow_heat": -93946.61,
            "aggregate_flow_electricity": 693.926,
            "aggregate_flow_costs": {"heat": -9667774.533, "electricity": 499869.622},
            "total_capital_cost": 93537260.0,
            "maintenance_labor_chemical_operating_cost": 0.0,
            "total_operating_cost": -6744368.918,
            "aggregate_direct_capital_cost": 93537260.0,
        }

        trough_costing_dict = {
            "capital_cost": 93537260.0,
            "variable_operating_cost": 823535.991,
            "fixed_operating_cost": 1600000.0,
            "direct_cost": 93304000.0,
            "direct_capital_cost": 93537260.0,
        }

        for (blk, results_dict) in [
            (m.fs.costing, sys_costing_dict),
            (m.fs.trough.costing, trough_costing_dict),
        ]:

            for v, r in results_dict.items():
                cv = getattr(blk, v)
                if cv.is_indexed():
                    for i, s in r.items():
                        assert pytest.approx(s, rel=1e-1) == value(cv[i])
                else:
                    assert pytest.approx(r, rel=1e-1) == value(cv)


class TestTroughSmall:
    @pytest.fixture(scope="class")
    def trough_small_heat_load(self):
        m = ConcreteModel()
        m.fs = FlowsheetBlock(dynamic=False)
        m.fs.trough = TroughSurrogate(heat_load_range=[10, 100])
        return m

    @pytest.mark.component
    @pytest.mark.skip
    def test_create_rbf_surrogate_small(self, trough_small_heat_load):
        m = trough_small_heat_load
        data = get_data(m.fs.trough.heat_load.bounds)
        test_surrogate_filename = os.path.join(
            os.path.dirname(__file__), "test_surrogate.json"
        )
        m.fs.trough._create_rbf_surrogate(output_filename=test_surrogate_filename)

        assert (
            m.fs.trough.rbf_train.get_result("heat_annual_scaled").metrics["R2"] > 0.99
        )
        assert (
            m.fs.trough.rbf_train.get_result("heat_annual_scaled").metrics["RMSE"]
            < 0.005
        )
        assert (
            m.fs.trough.rbf_train.get_result("electricity_annual_scaled").metrics["R2"]
            > 0.99
        )
        assert (
            m.fs.trough.rbf_train.get_result("electricity_annual_scaled").metrics[
                "RMSE"
            ]
            < 0.005
        )

        assert os.path.getsize(test_surrogate_filename) > 1e4
        os.remove(test_surrogate_filename)
        assert isinstance(m.fs.trough.surrogate, PysmoSurrogate)
        test_output = m.fs.trough.surrogate.evaluate_surrogate(data["validation"])

        expected_heat_annual_test = data["validation"]["heat_annual"]
        expected_electricity_annual_test = data["validation"]["electricity_annual"]
        predicted_heat_annual = test_output["heat_annual_scaled"] / value(
            m.fs.trough.heat_annual_scaling
        )
        predicted_electricity_annual = test_output["electricity_annual_scaled"] / value(
            m.fs.trough.electricity_annual_scaling
        )

        tol = 3e-2
        assert list(predicted_heat_annual) == pytest.approx(
            expected_heat_annual_test.tolist(), tol
        )
        assert list(predicted_electricity_annual) == pytest.approx(
            expected_electricity_annual_test.tolist(), tol
        )

    @pytest.mark.component
    @pytest.mark.skip
    def test_validation_small(self, trough_small_heat_load):
        m = trough_small_heat_load
        data = get_data(m.fs.trough.heat_load.bounds)
        heat_annual_list = []
        electricity_annual_list = []

        expected_heat_annual = data["validation"]["heat_annual"]
        expected_electricity_annual = data["validation"]["electricity_annual"]

        for row in data["validation"].itertuples():
            m.fs.trough.heat_load.fix(row.heat_load)
            m.fs.trough.hours_storage.fix(row.hours_storage)
            m.fs.trough.initialize_build()
            results = solver.solve(m, tee=True)
            assert_optimal_termination(results)
            heat_annual_list.append(value(m.fs.trough.heat_annual))
            electricity_annual_list.append(value(m.fs.trough.electricity_annual))

        # ensure surrogate model gives same results when inside a flowsheet
        assert heat_annual_list == pytest.approx(expected_heat_annual.tolist(), 1.05e-2)
        assert electricity_annual_list == pytest.approx(
            expected_electricity_annual.tolist(), 1.05e-2
        )

    @pytest.mark.unit
    @pytest.mark.skip
    def test_dof_small(self, trough_small_heat_load):
        m = trough_small_heat_load
        m.fs.trough.heat_load.fix(500)
        m.fs.trough.hours_storage.fix(12)
        assert degrees_of_freedom(m) == 0
        m.fs.trough.heat_load.unfix()
        m.fs.trough.hours_storage.unfix()
        assert degrees_of_freedom(m) == 2

    @pytest.mark.unit
    @pytest.mark.skip
    def test_calculate_scaling_small(self, trough_small_heat_load):
        m = trough_small_heat_load
        calculate_scaling_factors(m)
        assert len(list(unscaled_variables_generator(m))) == 0

    @pytest.mark.component
    @pytest.mark.skip
    def test_initialization_small(self, trough_small_heat_load):
        initialization_tester(
            trough_small_heat_load, unit=trough_small_heat_load.fs.trough, dof=2
        )

    @pytest.mark.component
    @pytest.mark.skip
    def test_solve_small(self, trough_small_heat_load):
        results = solver.solve(trough_small_heat_load)
        assert_optimal_termination(results)

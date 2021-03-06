#!/usr/bin/python
# -*- coding: utf-8 -*-
# Генератор sql запросов для простых логических формул
################################################################################

from collections import defaultdict
import operator

import logic_ast_nodes as nodes

class SqlGenerator:
    SYMBOL_MAPPING = {
        "Capital"        : 'capital',
        "City"           : 'city',
        "Country"        : 'country',
        "In"             : 'in_relation',
        "Neighbor"       : 'neighbor',
        "Largest"        : 'largest'        
    }


    def __init__(self):
        self.type = None

        self.tables = list()
        self.variables = defaultdict(set)
        self.constraints = []

        self.stack = []
        self.where_clause_stack = []


    def is_insert(self, node):
        return \
            isinstance(node, nodes.Application) or \
            isinstance(node, nodes.Negation) or \
            isinstance(node, nodes.And) or \
            isinstance(node, nodes.Or)


    def is_select(self, node):
        return \
            isinstance(node, nodes.Lambda)

    
    def is_count (self, node):
        return isinstance (node, nodes.Application) and \
               isinstance (node.function, nodes.Symbol) and \
               node.function.name == "Count"

    
    def is_bool (self, node):
        return isinstance (node, nodes.Application) and \
               isinstance (node.function, nodes.Symbol) and \
               node.function.name == "Bool"


    def resolve_column(self, table, n):
        return "arg%d" % n

    
    def resolve_table(self, table):
        if isinstance(table, str):
            return str

        elif isinstance(table, nodes.Symbol):
            n = self.SYMBOL_MAPPING[table.name]
            t = "alias%d_%s" % (len(self.tables), n)
            self.tables.append((n, t))
            return t
        else:
            raise RuntimeError, "Unable to deduce table name from value: {0}".format(repr(table))


    def resolve_value(self, value):
        if isinstance(value, str):
            return repr(value)
        elif isinstance(value, tuple):
            assert(len(value) >= 2)
            return "%s.%s" % (value[0], self.resolve_column(value[0], value[1]))
        elif isinstance(value, nodes.Symbol):
            return repr(value.name)
        else:
            raise RuntimeError, "Unable to deduce table name from value: {0}".format(repr(value))


    def _visit_function(self, node):
        if isinstance(node, nodes.Application):
            table, values = node.uncurry()
            table = self.resolve_table(table)

            for n, value in enumerate(values):
                if isinstance(value, nodes.Symbol):
                    self.constraints.append((table, n, value))
                elif isinstance(value, nodes.Variable):
                    self.variables[value.name].add((table, n))
        elif isinstance(node, nodes.And):
            node.visit(self._visit_function, self._visit_combinator, None)
        elif isinstance(node, nodes.Negation):
            raise RuntimeError, "'Not' clauses are not supported currently."
        elif isinstance(node, nodes.Or):
            raise RuntimeError, "'Or' clauses are not supported currently."
        else:
            raise RuntimeError, "Unsupported node: {0}".format(repr(node))

    def _make_where_clause(self, node):
        if isinstance(node, nodes.Application):
            table, values = node.uncurry()
            table = self.resolve_table(table)
            
            result = ""

            for n, value in enumerate(values):
                if isinstance(value, nodes.Symbol):
                    if (result != ""):
                        result += " AND "
                    result += "%s.arg%d = '%s'" % (table, n, value) 
                elif isinstance(value, nodes.Variable):
                    self.variables[value.name].add((table, n))
            
            if result == "":
                return None
            else:
                return "(" + result + ")"
        elif isinstance(node, nodes.And):
            lhs_value = self._make_where_clause(node.lhs)
            rhs_value = self._make_where_clause(node.rhs)
            if lhs_value is None and rhs_value is None:
                return None
            elif lhs_value is None:
                return rhs_value
            elif rhs_value is None:
                return lhs_value
            else:
                return "%s AND %s" % (lhs_value, rhs_value)                

        elif isinstance(node, nodes.Negation):
            body_value = self._make_where_clause(node.body)
            if body_value is None:
                return None
            else:
                return "NOT %s" % (body_value)                

        elif isinstance(node, nodes.Or):
            lhs_value = self._make_where_clause(node.lhs)
            rhs_value = self._make_where_clause(node.rhs)
            if lhs_value is None and rhs_value is None:
                return None
            elif lhs_value is None:
                return rhs_value
            elif rhs_value is None:
                return lhs_value
            else:
                return "%s OR %s" % (lhs_value, rhs_value)                
        else:
            raise RuntimeError, "Unsupported node: {0}".format(repr(node))

    def  _visit_combinator(self, *args):
        pass


    def _induce_variable_constraints(self):
        for variable in self.variables:
            variable_constraints = list(self.variables[variable])
            for lhs, rhs in zip(variable_constraints[0:], variable_constraints[1:]):
                self.constraints.append((lhs[0], lhs[1], rhs))


    def make_insert(self, node):
        self.type = "INSERT OR IGNORE"
        self._visit_combinator(self._visit_function(node))

        reverse_table_mapping = dict(map(lambda x: (x[1], x[0]), self.tables))

        inserted_values = defaultdict(list)

        for table, column, value in self.constraints:
            inserted_values[table].append( (self.resolve_column(table, column), self.resolve_value(value)) )
        for table in inserted_values.iterkeys():
            columns_and_values = inserted_values[table]

            columns = map(operator.itemgetter(0), columns_and_values)
            values = map(operator.itemgetter(1), columns_and_values)

            table_clause = "%s(%s)" % (reverse_table_mapping[table], ", ".join(columns))
            values_clause = "(%s)" % (", ".join(values))

            yield "INSERT OR IGNORE INTO %s VALUES %s" % (table_clause, values_clause)


    def make_select(self, node):
        self.type = "SELECT"

        body = node.uncurry() [1]
        
        where_clause_constants = self._make_where_clause(body)
        self._induce_variable_constraints()

        result_clause = ", ".join(map(
            lambda kv: "%s AS %s" % (self.resolve_value(list(kv[1])[0]), kv[0]),
            self.variables.items()))
        from_clause = ", ".join(map(
            lambda t: "%s AS %s" % t,
            self.tables))
        where_clause_variables = " AND ".join(map(
            lambda c: "%s = %s" % (self.resolve_value(c[0:2]), self.resolve_value(c[2])), 
            self.constraints))
        
        total_where_clause = "(%s) AND (%s)" %(where_clause_constants, where_clause_variables)

        yield "SELECT {0} FROM {1} WHERE {2}".format(result_clause, from_clause, total_where_clause)
    
        
    def make_count (self, node):
        self.type = "COUNT"

        body = node.argument.uncurry() [1]        
        self._visit_combinator(self._visit_function(body))       
        self._induce_variable_constraints()
        
        from_clause = ", ".join(map(
            lambda t: "%s AS %s" % t,
            self.tables))
        where_clause = " AND ".join(map(
            lambda c: "%s = %s" % (self.resolve_value(c[0:2]), self.resolve_value(c[2])), 
            self.constraints))

        yield "SELECT count(1) FROM {0} WHERE {1}".format(from_clause, where_clause)
    
    
    def make_bool (self, node):
        self.type = "BOOL"

        self._visit_combinator(self._visit_function(node.argument))
        self._induce_variable_constraints()
        
        from_clause = ", ".join(map(
            lambda t: "%s AS %s" % t,
            self.tables))
        where_clause = " AND ".join(map(
            lambda c: "%s = %s" % (self.resolve_value(c[0:2]), self.resolve_value(c[2])), 
            self.constraints))

        yield "SELECT case when count(1) THEN \"yes\"  ELSE \"no\" END FROM {0} WHERE {1}".format(from_clause, where_clause)


    def make_sql(self, node):
        generator = None

        if self.is_count(node):
            generator = self.make_count(node)
        elif self.is_bool(node):
            generator = self.make_bool (node)
        elif self.is_insert(node):
            generator = self.make_insert(node)
        elif self.is_select(node):
            generator = self.make_select(node)
        else:
            raise RuntimeError, "Unable to determine SQL query type; probably expression is too complex."

        for item in generator:
            yield item
